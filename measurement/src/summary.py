"""Read-only summary of a personal measurement result."""
import argparse
import csv
import json
import math
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


def number(value, unit="", digits=2):
    try:
        value = float(value)
        if not math.isfinite(value):
            return "미기록"
    except (TypeError, ValueError):
        return "미기록"
    return f"{value:,.{digits}f}{unit}"


def gib(value):
    return number(None if value is None else float(value) / 1024**3, " GiB", 3)


def find_folder(base, ident=None):
    if ident:
        if ident in (".", "..") or Path(ident).name != ident or "/" in ident or "\\" in ident:
            raise ValueError("요청 ID만 입력하세요. 경로는 --settings로 설정합니다.")
        folder = base / ident
        if not folder.is_dir():
            raise ValueError(f"결과 폴더가 없습니다: {ident}")
        return folder
    if not base.is_dir():
        raise ValueError(f"아직 측정 결과가 없습니다: {base}")
    folders = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    if not folders:
        raise ValueError("아직 측정 결과가 없습니다.")
    return folders[0]


def show(folder):
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        raise ValueError(f"meta.json이 없습니다: {folder} (run.log/error.txt를 확인하세요)")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not meta.get("finished"):
        raise ValueError(f"최근 결과가 미완료 상태입니다: {folder.name}\n실행 중이거나 중단됐을 수 있습니다. 완료 후 다시 실행하거나 ./summary 요청ID로 이전 결과를 선택하세요.")
    print(f"결과: {folder.name}\n폴더: {folder}")
    print(f"모델: {meta.get('model_file', '미기록')} | 성공 {meta.get('ok', '미기록')} / 실패 {meta.get('failed', '미기록')}")
    path = folder / "results.csv"
    rows = []
    if path.is_file():
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    conditions = meta.get("cpu_monitor", {}).get("conditions", [])
    items = [(row, next((c for c in conditions if str(c.get("order")) == row.get("order")), {})) for row in rows]
    result_orders = {row.get("order") for row in rows}
    items.extend(({}, c) for c in conditions if str(c.get("order")) not in result_orders)
    for row, condition in items:
        test = row.get("test", condition.get("test", "미기록"))
        threads = row.get("threads", condition.get("threads", "미기록"))
        repeat = row.get("repeat", condition.get("repeat", "미기록"))
        print(f"\n[{test}] 스레드 {threads} | 내부 반복 {repeat}")
        if row.get('load_mode'):
            print('  모델 읽기 방식      : '+row['load_mode'])
        if row:
            print("  TPS 평균 ± 표준편차 : " + number(row.get("avg_ts")) + " ± " + number(row.get("stddev_ts")))
        else:
            print("  TPS                 : 결과 없음 (run.log 확인)")
        loading = condition.get("loading", {})
        if not loading:
            loading = next((c for c in meta.get("loading_measurement", {}).get("conditions", []) if str(c.get("order")) == row.get("order")), {})
        print("  준비 시간           : " + number(loading.get("startup_to_warmup_s"), "초", 3))
        print("  CPU 평균 / 관측 최대: " + number(condition.get("avg_cpu_percent"), "%") + " / " + number(condition.get("max_cpu_percent"), "%"))
        print("  평균 보고 클럭      : " + number(condition.get("frequency", {}).get("mean_sample_avg_mhz"), " MHz"))
        memory = condition.get("memory", {})
        print("  RSS 평균 / 관측 최대: " + gib(memory.get("mean_sample_rss_bytes")) + " / " + gib(memory.get("max_observed_rss_bytes")))
        print("  스왑 관측 최대      : " + gib(memory.get("max_observed_swap_bytes")))
        counters = condition.get("io_and_faults", {})
        for key, label, unit in (("io_read_bytes", "디스크 읽기량", " bytes"), ("io_rchar_bytes", "read 계열 읽기량", " bytes"), ("minor_faults", "minor 페이지 폴트", "회"), ("major_faults", "major 페이지 폴트", "회")):
            counter = counters.get(key, {})
            value = counter.get("last_observed_total")
            note = ""
            if value is not None and not counter.get("final_observed"):
                note = " (마지막 관측값 · " + number(counter.get("last_observed_elapsed_s"), "초", 2) + ")"
            print(f"  {label}: " + number(value, unit, 0) + note)
    if not items:
        print("\n기록된 측정값이 없습니다. run.log/error.txt를 확인하세요.")
    loading=meta.get('model_loading',{})
    if loading.get('measurement_method') == 'gnu_time_load_process_v1':
        print('\n[모델 로딩 시간 · time 로딩 전용 프로세스]')
        print('  시작·백엔드·모델 로드·종료 포함 | time 해상도 0.01초 | 이전 API 시간과 직접 비교 금지')
    else:
        print('\n[모델 로딩 시간 · 로드 함수 호출→반환 (이전 방식)]')
    if not loading.get('conditions'):
        print('  '+('비활성화' if loading.get('status')=='disabled' else '미기록'))
    for item in loading.get('conditions',[]):
        print(f"  읽기 {item['load_mode']} | 캐시 {item['cache']} | 성공 {item['ok']} / 실패 {item['failed']}")
        print('  평균 ± 표준편차: '+number(item.get('mean_s'),'초',3)+' ± '+number(item.get('stddev_s'),'초',3)+' | CV: '+number(item.get('cv_percent'),'%'))
    if loading.get('failed'):
        print('  실패 사유: model_loading.csv / model-loading.log 확인')
    print("\n참고: 준비 시간은 워밍업 직전까지입니다. 클럭은 허용 CPU의 보고값 평균입니다.")
    print('새 로딩 시간은 프로세스 초기화·정리를 포함하고 추론은 제외합니다. mmap의 지연 읽기는 이후 추론으로 넘어갈 수 있습니다.')
    print("RSS 최대는 관측값이며 순간 피크를 놓칠 수 있습니다. read 계열 읽기량은 mmap 접근 전체가 아닙니다.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="개인 측정 결과 요약 (인자 없이 최근 결과)")
    parser.add_argument("id", nargs="?", help="결과 폴더의 요청 ID")
    parser.add_argument("--settings", type=Path, default=ROOT / "settings.yaml")
    args = parser.parse_args(argv)
    try:
        with args.settings.open(encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        if not isinstance(settings, dict) or not isinstance(settings.get("work_dir"), str):
            raise ValueError("settings.yaml에 work_dir 경로가 필요합니다.")
        base = Path(settings["work_dir"]).expanduser()
        if not base.is_absolute():
            base = args.settings.resolve().parent / base
        return show(find_folder(base / "results", args.id))
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
