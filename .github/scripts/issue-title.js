// 이슈 제목 앞의 [Feature] 같은 종류 접두사는 type: 라벨과 겹치므로 번호로 바꾼다.
function numberedTitle(title, number) {
  if (/^\[PP-\d+\]/.test(title)) return title;
  const rest = title.replace(/^\s*\[[^\]]*\]\s*/, "").trim();
  return `[PP-${String(number).padStart(3, "0")}] ${rest}`;
}

module.exports = { numberedTitle };
