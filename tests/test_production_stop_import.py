"""验证落料线与点焊线的异常停机导入及月累计运行状态。"""

from pathlib import Path

import pytest

from backend.production_import import HOUR_ROWS, LAYOUTS, STOP_ROWS, parse_daily_hours
from tests.test_api import production_payload
from tests.test_production_import import preview, production_workbook


@pytest.mark.parametrize('identifier,values,expected', [
    (1, [' 1.5 h '], 1.5), (1, [None], 0),
    (2, [1, '2H', 3, 0.5, 0.25, None], 6.75), (2, [None] * 6, 0),
])
def test_cutting_and_spot_welding_stops_are_imported(client, identifier, values, expected):
    """验证两线停机汇总；参数为客户端、产线、源值及预期小时；断言预览、保存与大屏百分比一致。"""
    def change(book):
        """填充停机明细；book 为测试工作簿；无返回值，按模板映射写入首日。"""
        sheet = book[LAYOUTS[identifier - 1][1]]
        for (row, _), value in zip(STOP_ROWS[identifier], values):
            sheet.cell(row, 3).value = value
    data = preview(client, production_workbook(change)).get_json()
    item = next(item for item in data['items'] if item['line_id'] == identifier)
    assert item['abnormal_hours'] == expected and not item['warnings']
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    line = client.get('/api/dashboard').get_json()['lines'][identifier - 1]
    assert line['monthly_abnormal_hours'] == expected
    planned = 60 if identifier == 2 else 20
    assert line['monthly_planned_hours'] == planned
    assert line['runtime_rate'] == round((planned - expected) / planned * 100, 2)


@pytest.mark.parametrize('identifier', [1, 2])
@pytest.mark.parametrize('kind', ['missing_row', 'negative', 'cache', 'text', 'exceeds_plan'])
def test_invalid_stop_fields_warn_without_fabricating_zero(client, identifier, kind):
    """验证停机异常；参数为客户端、产线及错误类型；断言缺行、错误缓存、负数和越界不能生成零停机。"""
    def change(book):
        """注入停机错误；book 为测试工作簿；无返回值，保留其他有效数量。"""
        sheet = book[LAYOUTS[identifier - 1][1]]
        row = STOP_ROWS[identifier][0][0]
        if kind == 'missing_row':
            sheet.cell(row, 2).value = None
        else:
            sheet.cell(row, 3).value = {'negative': -1, 'cache': '=1/0', 'text': '待核对', 'exceeds_plan': 21}[kind]
    data = preview(client, production_workbook(change)).get_json()
    item = next(item for item in data['items'] if item['line_id'] == identifier)
    assert item['warnings'] and item['abnormal_hours'] is None
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    line = client.get('/api/dashboard').get_json()['lines'][identifier - 1]
    assert line['monthly_qty'] is not None and line['runtime_rate'] is None


def test_invalid_source_plan_preserves_existing_hours_and_imports_valid_stop(client):
    """验证源计划异常；client 为客户端；断言保留已有十二小时计划，同时导入有效停机并给出警告。"""
    draft = production_payload(client, '2026-11-01')
    draft['lines'][1]['planned_hours'] = 12
    assert client.put('/api/production/2026-11-01', json=draft).status_code == 200
    def change(book):
        """模拟源计划负值；book 为测试工作簿；无返回值，停机明细仍有效。"""
        sheet = book[LAYOUTS[1][1]]
        sheet.cell(HOUR_ROWS[2][0], 3).value = -4
        sheet.cell(STOP_ROWS[2][3][0], 3).value = 5.25
    data = preview(client, production_workbook(change)).get_json()
    data['items'] = [item for item in data['items'] if item['line_id'] == 2]
    item = data['items'][0]
    assert 'planned_hours' not in item and item['warnings'] and item['abnormal_hours'] == 5.25
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    line = client.get('/api/editor?date=2026-11-01').get_json()['production']['lines'][1]
    assert line['planned_hours'] == 12 and line['abnormal_hours'] == 5.25


def test_real_file_import_restores_both_monthly_runtime_rates(client):
    """验证原表两线；client 为隔离客户端；断言三十二条停机自动提取，异常源计划保留默认并显示月累计。"""
    source = next((Path(__file__).resolve().parents[1] / 'docs').glob('5.*.xlsx'), None)
    if source is None:
        pytest.skip('当前环境没有用户提供的生产日报')
    with source.open('rb') as stream:
        data = preview(client, stream).get_json()
    data['items'] = [item for item in data['items'] if item['line_id'] in (1, 2)]
    assert len(data['items']) == 32
    assert all(item['abnormal_hours'] is not None for item in data['items'])
    assert client.post('/api/production/2026-11/import', json=data).status_code == 200
    lines = client.get('/api/dashboard').get_json()['lines']
    assert lines[0]['monthly_abnormal_hours'] == 12 and lines[0]['runtime_rate'] == 96.32
    assert lines[1]['monthly_abnormal_hours'] == pytest.approx(68 + 2 / 3)
    assert lines[1]['monthly_planned_hours'] == 990 and lines[1]['runtime_rate'] == 93.06


@pytest.mark.parametrize('day,formulas,formats,expected', [
    (1, {(45, 3): '=C44/C43'}, {}, None),
    (2, {(45, 3): '=C44/C43'}, {}, 0.5),
    (31, {(45, 33): '=AG44/AG43'}, {}, None),
    (1, {(45, 3): '=SUM(C44)/C43'}, {(45, 3): '0.00%'}, None),
])
def test_welding_stop_validation_uses_the_same_excel_column(day, formulas, formats, expected):
    """验证停机公式坐标；参数为日序、公式、格式及预期；断言首尾日校验准确，前日公式不污染次日。"""
    data = [[None] * 33 for _ in range(46)]
    for row in HOUR_ROWS[3]:
        data[row - 1][1] = '工时（H）'
        data[row - 1][day + 1] = 10
    for row in (28, 33, 41, 45):
        data[row - 1][1] = '异常停机工时'
    # 使用真实缓存小数，确保测试能发现把报废率误当小时的问题，而非只测缺失缓存。
    data[44][day + 1] = 0.5
    result = parse_daily_hours(data, day, 3, LAYOUTS[2][1], formulas, formats)
    assert result['abnormal_hours'] == expected
    assert bool(result['warnings']) is (expected is None)
