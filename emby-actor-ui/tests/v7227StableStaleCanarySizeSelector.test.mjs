import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import {
  STALE_DELETE_CANARY_DEFAULT_SIZE,
  staleDeleteCanarySelectedTotal,
  validateStaleDeleteCanarySize,
} from '../src/utils/staleDeleteCanary.js';

const source = fs.readFileSync(
  new URL('../src/components/PersonCleanupPage.vue', import.meta.url),
  'utf8',
);

test('new page defaults every Canary input to 10', () => {
  assert.equal(STALE_DELETE_CANARY_DEFAULT_SIZE, 10);
  assert.match(source, /ref\(STALE_DELETE_CANARY_DEFAULT_SIZE\)/);
});

for (const value of [1, 10, 100]) {
  test(`integer ${value} is accepted and sent through the existing limit field`, () => {
    assert.equal(validateStaleDeleteCanarySize(value), true);
    assert.match(source, /\{ limit: staleDeleteCanarySize\.value \}/);
  });
}

for (const value of [0, 101, 1.5, null, '', '10']) {
  test(`invalid Canary value ${JSON.stringify(value)} cannot be submitted`, () => {
    assert.equal(validateStaleDeleteCanarySize(value), false);
    assert.match(source, /if \(!validateStaleDeleteCanarySize\(staleDeleteCanarySize\.value\)\)/);
  });
}

test('selector exposes the exact visible safety bounds and guidance', () => {
  assert.match(source, /:min="1"/);
  assert.match(source, /:max="100"/);
  assert.match(source, /:step="1"/);
  assert.match(source, /首次生产验证建议使用 10。单个 Canary job 后端硬限制最多100人。/);
});

test('requested 10 and backend-selected 8 remain distinct in the UI', () => {
  assert.equal(staleDeleteCanarySelectedTotal({ selected_total: 8 }), 8);
  assert.match(source, /请求 Canary/);
  assert.match(source, /requested_limit/);
  assert.match(source, /实际选中/);
  assert.match(source, /selected_total/);
});

test('confirmation uses backend selected_total instead of the local input', () => {
  assert.match(source, /本次将最多删除 \{\{ staleDeleteCanarySelectedTotal \}\} 位 Person/);
  assert.match(source, /该数量来自后端固定预览，不使用当前输入值/);
  assert.doesNotMatch(source, /本次将最多删除 \{\{ staleDeleteCanarySize/);
});

test('ordinary Person Cleanup controls remain present', () => {
  assert.match(source, /一键安全清理/);
  assert.match(source, /历史安全任务/);
  assert.match(source, /删除选中/);
});
