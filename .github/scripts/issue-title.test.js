const test = require("node:test");
const assert = require("node:assert");
const { numberedTitle } = require("./issue-title");

test("번호를 세 자리로 붙인다", () => {
  assert.strictEqual(numberedTitle("측정 잠금 추가", 7), "[PP-007] 측정 잠금 추가");
  assert.strictEqual(numberedTitle("측정 잠금 추가", 1234), "[PP-1234] 측정 잠금 추가");
});

test("종류 접두사를 번호로 바꾼다", () => {
  assert.strictEqual(numberedTitle("[Feature] 측정 잠금 추가", 23), "[PP-023] 측정 잠금 추가");
  assert.strictEqual(numberedTitle("[Bug]두 측정이 동시에 실행됨", 24), "[PP-024] 두 측정이 동시에 실행됨");
});

test("이미 번호가 있으면 그대로 둔다", () => {
  assert.strictEqual(numberedTitle("[PP-023] 측정 잠금 추가", 23), "[PP-023] 측정 잠금 추가");
});

test("제목 중간의 대괄호는 건드리지 않는다", () => {
  assert.strictEqual(numberedTitle("제목에 [PP-NNN] 번호 붙이기", 23), "[PP-023] 제목에 [PP-NNN] 번호 붙이기");
});
