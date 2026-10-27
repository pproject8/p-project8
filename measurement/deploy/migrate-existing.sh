#!/bin/bash
# Existing /srv/pp8 installation -> this Git checkout. Run as sudo only after tests.
set -euo pipefail
BASE=/srv/pp8
REPO=$BASE/repo
if [ "$(id -u)" -ne 0 ]; then
    echo 'sudo bash /srv/pp8/repo/measurement/deploy/migrate-existing.sh 로 실행하세요.' >&2
    exit 1
fi
exec 9>"$BASE/.migration.lock"
flock -n 9 || { echo '다른 전환 작업이 실행 중입니다.' >&2; exit 1; }
test -f "$REPO/measurement/src/team.py"
test -f "$BASE/settings.yaml"
test -f /etc/systemd/system/pp8-worker.service
test -f /usr/local/bin/pp8
if find "$BASE/work/queue" "$BASE/work/running" -maxdepth 1 -name '*.yaml' -print -quit | grep -q .; then
    echo '대기 또는 실행 중 요청이 있습니다. 제출을 잠시 중단하고 완료 후 다시 실행하세요.' >&2
    exit 1
fi
BACKUP=$BASE/backups/$(date +%Y%m%d-%H%M%S)-git-migration
install -d -m 2750 -o jungwon -g pp8 "$BACKUP"
cp -a /etc/systemd/system/pp8-worker.service "$BACKUP/pp8-worker.service"
cp -a /usr/local/bin/pp8 "$BACKUP/pp8"
cp -a "$BASE/settings.yaml" "$BACKUP/settings.yaml"
systemctl is-active --quiet pp8-worker || { echo '기존 서비스가 active인지 확인하세요.'; exit 1; }
systemctl stop pp8-worker
rollback() {
    code=$?
    trap - ERR
    echo "전환 실패. 이전 서비스로 복구합니다. 백업: $BACKUP" >&2
    cp -a "$BACKUP/pp8-worker.service" /etc/systemd/system/pp8-worker.service
    cp -a "$BACKUP/pp8" /usr/local/bin/pp8
    systemctl daemon-reload
    systemctl start pp8-worker || true
    exit "$code"
}
trap rollback ERR
# Capture state only after stopping the worker. Existing results are never moved/deleted.
tar -C "$BASE" -czf "$BACKUP/runtime-backup.tar.gz" team-src requests code settings.yaml work
chgrp pp8 "$BACKUP/runtime-backup.tar.gz"
chmod 640 "$BACKUP/runtime-backup.tar.gz"
install -m 644 "$REPO/measurement/deploy/pp8-worker.service" /etc/systemd/system/pp8-worker.service
cat > "$BACKUP/pp8.new" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /srv/pp8/repo/measurement/src/team.py --settings /srv/pp8/settings.yaml "$@"
EOF
install -m 755 "$BACKUP/pp8.new" /usr/local/bin/pp8
systemctl daemon-reload
systemctl start pp8-worker
sleep 2
systemctl is-active --quiet pp8-worker
pp8 status
trap - ERR
echo "Git 코드로 전환 완료. 백업: $BACKUP"
echo '기존 team-src/code 폴더와 모델/결과는 보존했습니다. 짧은 요청으로 최종 확인하세요.'
