const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../../static/js/api.js');

test('Excel 上传保留文件表单并让浏览器生成边界', async () => {
  const formData = new FormData();
  formData.append('file', new Blob(['Excel 测试']), '库存.xls');
  global.fetch = async (path, options) => {
    assert.equal(path, '/api/inventory/preview');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers, undefined);
    assert.equal(options.body, formData);
    return { ok: true, json: async () => ({ raw: [], semi: [] }) };
  };
  assert.deepEqual(await api.request('/api/inventory/preview', { method: 'POST', formData }), { raw: [], semi: [] });
});

test('保存请求完整携带中文字段与零值', async () => {
  global.fetch = async (path, options) => {
    assert.equal(path, '/api/inventory');
    assert.equal(options.method, 'PUT');
    assert.equal(options.cache, 'no-store');
    assert.deepEqual(JSON.parse(options.body), { version: 0, material: '钢卷', quantity: 0 });
    return { ok: true, json: async () => ({ version: 1 }) };
  };
  const response = await api.request('/api/inventory', { method: 'PUT', body: { version: 0, material: '钢卷', quantity: 0 } });
  assert.equal(response.version, 1);
});

test('版本冲突保留接口的中文错误及状态码', async () => {
  global.fetch = async () => ({ ok: false, status: 409, json: async () => ({ error: '数据已变化' }) });
  await assert.rejects(api.request('/api/inventory'), error => error.status === 409 && error.message === '数据已变化');
});

test('网络失败转换成可理解的错误', async () => {
  global.fetch = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(api.request('/api/dashboard'), /无法连接服务/);
});

test('超时不会被当成保存成功', async () => {
  global.fetch = async () => { const error = new Error('aborted'); error.name = 'AbortError'; throw error; };
  await assert.rejects(api.request('/api/dashboard'), /请求超时/);
});

test('错误格式的响应不会覆盖页面数据', async () => {
  global.fetch = async () => ({ ok: true, json: async () => { throw new SyntaxError('invalid'); } });
  await assert.rejects(api.request('/api/dashboard'), /服务返回格式不正确/);
});
