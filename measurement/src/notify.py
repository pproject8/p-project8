"""Discord webhook notices for the shared worker. Never interrupts measurement."""
import json
import sys
import urllib.request

LIMIT = 2000  # Discord message content limit


def send(settings, content):
    url = settings.get("discord_webhook_url")
    if not url:
        return False
    # 요청 YAML과 예외 문구가 그대로 들어가므로 @everyone 같은 멘션을 막는다.
    body = json.dumps({"content": content[:LIMIT], "allowed_mentions": {"parse": []}}).encode("utf-8")
    # Discord rejects urllib's default User-Agent.
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "pp8-worker"})
    try:
        with urllib.request.urlopen(request, timeout=5):
            return True
    except Exception as exc:
        # 예외 문구에 웹훅 토큰이 섞일 수 있어 종류와 상태 코드만 남긴다.
        print(f"알림 전송 실패: {type(exc).__name__} {getattr(exc, 'code', '')}".rstrip(), file=sys.stderr, flush=True)
        return False
