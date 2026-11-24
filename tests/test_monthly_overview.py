"""全局生产指标的月汇总与按日编辑回归测试，全部使用隔离数据库。"""

from datetime import datetime, timezone

from playwright.sync_api import expect

from tests.test_api import production_payload, settings_payload


def set_standards(client, first=100, second=300):
    """配置拼焊标准；client 为客户端、first/second 为 A/B 日标准；断言配置保存成功。"""
    draft = settings_payload(client)
    draft['lines'][2]['standard_qty'] = first
    draft['lines'][3]['standard_qty'] = second
    assert client.put('/api/settings', json=draft).status_code == 200


def save_overview(client, day, first=None, second=None, first_defects=None, second_defects=None,
                  first_hours=24, second_hours=24, **values):
    """保存测试日报；client/day 为客户端和日期、first/second 及对应 defects/hours 为产量、报废和工时；返回草稿。"""
    draft = production_payload(client, day)
    draft['lines'][2]['actual_qty'] = first
    draft['lines'][3]['actual_qty'] = second
    draft['lines'][2]['defect_qty'] = first_defects
    draft['lines'][3]['defect_qty'] = second_defects
    draft['lines'][2]['planned_hours'] = first_hours
    draft['lines'][3]['planned_hours'] = second_hours
    draft['overview'].update(values)
    assert client.put('/api/production/' + day, json=draft).status_code == 200
    return draft


def test_monthly_overview_sums_quantities_and_averages_daily_values(client):
    """验证月汇总和日编辑；client 为客户端；断言累计比率、独立均值及历史日值。"""
    set_standards(client)
    save_overview(client, '2026-10-31', first=9999, oee_percent=100)
    save_overview(client, '2026-11-01', first=50, first_defects=5,
                  oee_percent=90, quality_percent=99, average_output=30)
    save_overview(client, '2026-11-20', first=100, second=200, first_defects=0, second_defects=2,
                  oee_percent=80, quality_percent=97, average_output=60)
    save_overview(client, '2026-11-21', oee_percent=0, average_output=0)
    overview = client.get('/api/dashboard').get_json()['overview']
    assert overview == {'planned_qty': 500, 'actual_qty': 350, 'completion_rate': 70,
                        'oee_percent': None, 'quality_percent': 98, 'average_output': 4.86}
    original = production_payload(client, '2026-11-01')['overview']
    assert original['planned_qty'] == 100
    assert original['actual_qty'] == 50
    assert original['oee_percent'] is None
    # 修改历史某日后重新读取，月汇总应替换该日值，不能重复累计。
    save_overview(client, '2026-11-01', first=75)
    assert client.get('/api/dashboard').get_json()['overview']['actual_qty'] == 375


def test_monthly_overview_preserves_missing_and_zero(client):
    """验证缺失与零值；client 为客户端；断言空白不计计划、零产量计计划及零分母语义。"""
    set_standards(client)
    assert all(value is None for value in client.get('/api/dashboard').get_json()['overview'].values())
    save_overview(client, '2026-11-01', quality_percent=90)
    save_overview(client, '2026-11-02')
    assert client.get('/api/dashboard').get_json()['overview']['planned_qty'] is None
    save_overview(client, '2026-11-03', first=0, oee_percent=0, quality_percent=0, average_output=0)
    assert client.get('/api/dashboard').get_json()['overview'] == {
        'planned_qty': 100, 'actual_qty': 0, 'completion_rate': 0,
        'oee_percent': None, 'quality_percent': None, 'average_output': 0}
    set_standards(client, first=0)
    overview = client.get('/api/dashboard').get_json()['overview']
    assert overview['planned_qty'] == 0 and overview['completion_rate'] is None


def test_overview_ignores_other_processes_manual_totals_and_missing_standards(client):
    """验证统计来源；client 为客户端；断言不混加切割点焊、不接受手填总量且缺标准不算比率。"""
    set_standards(client, second=None)
    draft = save_overview(client, '2026-11-01', first=50, second=100)
    draft['lines'][0]['actual_qty'] = 10000.5
    draft['lines'][1]['actual_qty'] = 9999
    draft['overview'].update(planned_qty=99999, actual_qty=88888, quality_percent=100, average_output=99999)
    draft['version'] = client.get('/api/editor').get_json()['version']
    assert client.put('/api/production/2026-11-01', json=draft).status_code == 200
    for overview in [client.get('/api/dashboard').get_json()['overview'],
                     production_payload(client, '2026-11-01')['overview']]:
        assert overview['actual_qty'] == 150
        assert overview['planned_qty'] is None
        assert overview['completion_rate'] is None
        assert overview['quality_percent'] is None
        assert overview['average_output'] == 3.13


def test_average_output_uses_total_reported_line_hours(client):
    """验证每小时产出；client 为客户端；断言总量除以总工时、零产量计时和历史工时修正。"""
    save_overview(client, '2026-10-31', first=10000, first_hours=1)
    save_overview(client, '2026-11-01', first=80, second=120, first_hours=8, second_hours=4)
    save_overview(client, '2026-11-20', first=60, first_hours=12)
    assert production_payload(client, '2026-11-01')['overview']['average_output'] == 16.67
    assert client.get('/api/dashboard').get_json()['overview']['average_output'] == 10.83
    # 明确零产量也占用工时，未录入产量的 B 线不占用工时。
    save_overview(client, '2026-11-21', first=0, first_hours=6)
    assert client.get('/api/dashboard').get_json()['overview']['average_output'] == 8.67
    save_overview(client, '2026-11-21', first=0, first_hours=2.5)
    assert client.get('/api/dashboard').get_json()['overview']['average_output'] == 9.81


def test_average_output_handles_missing_and_zero_hours(client):
    """验证工时边界；client 为客户端；断言无日报、零工时、零产量和小数工时的每小时产出。"""
    assert client.get('/api/dashboard').get_json()['overview']['average_output'] is None
    save_overview(client, '2026-11-21', first=0, first_hours=0)
    assert client.get('/api/dashboard').get_json()['overview']['average_output'] is None
    save_overview(client, '2026-11-21', first=25, first_hours=2.5)
    assert client.get('/api/dashboard').get_json()['overview']['average_output'] == 10
    save_overview(client, '2026-11-21', first=0, first_hours=2.5)
    assert client.get('/api/dashboard').get_json()['overview']['average_output'] == 0


def test_monthly_quality_uses_total_good_quantity_and_requires_defect_data(client):
    """验证月合格率；client 为客户端；断言按总量加权、报废空值、显式零及历史修正。"""
    save_overview(client, '2026-10-31', first=100, first_defects=100)
    save_overview(client, '2026-11-01', first=10, first_defects=2)
    save_overview(client, '2026-11-20', first=90, second=100, first_defects=0, second_defects=0)
    overview = client.get('/api/dashboard').get_json()['overview']
    assert overview['quality_percent'] == 99
    assert production_payload(client, '2026-11-01')['overview']['quality_percent'] == 80
    save_overview(client, '2026-11-21', first=0)
    assert client.get('/api/dashboard').get_json()['overview']['quality_percent'] == 99
    # 部分已录入产量缺少报废时，不能用其余日报推断整月合格率。
    save_overview(client, '2026-11-21', first=50)
    assert client.get('/api/dashboard').get_json()['overview']['quality_percent'] is None
    save_overview(client, '2026-11-21', first=50, first_defects=0)
    assert client.get('/api/dashboard').get_json()['overview']['quality_percent'] == 99.2
    save_overview(client, '2026-11-01', first=10, first_defects=10)
    assert client.get('/api/dashboard').get_json()['overview']['quality_percent'] == 96


def test_quality_zero_good_and_future_defects(client, clock):
    """验证比率边界；client/clock 为客户端和时钟；断言全报废为零且未来日报不参与计算。"""
    save_overview(client, '2026-11-21', first=20, first_defects=20)
    assert client.get('/api/dashboard').get_json()['overview']['quality_percent'] == 0
    clock['now'] = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
    save_overview(client, '2026-11-22', first=100, first_defects=0)
    clock['now'] = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    assert client.get('/api/dashboard').get_json()['overview']['quality_percent'] == 0


def test_monthly_overview_excludes_future_and_switches_by_business_timezone(client, clock):
    """验证日期边界；参数为客户端和时钟；断言未来排除、业务时区跨月及新月清空。"""
    set_standards(client)
    clock['now'] = datetime(2026, 12, 1, 0, 30, tzinfo=timezone.utc)
    save_overview(client, '2026-11-21', first=50)
    save_overview(client, '2026-11-30', first=100)
    save_overview(client, '2026-12-01', first=300)
    clock['now'] = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    assert client.get('/api/dashboard').get_json()['overview']['actual_qty'] == 50
    assert client.get('/api/dashboard').get_json()['overview']['average_output'] == 2.08
    clock['now'] = datetime(2026, 12, 1, 0, 30, tzinfo=timezone.utc)
    snapshot = client.get('/api/editor').get_json()
    config = {'version': snapshot['version'], **snapshot['settings'],
              'timezone': 'America/New_York', 'lines': snapshot['production']['lines'],
              'processes': [node for node in snapshot['processes'] if node['id'] in (1, 5)]}
    assert client.put('/api/settings', json=config).status_code == 200
    dashboard = client.get('/api/dashboard').get_json()
    assert dashboard['business_date'] == '2026-11-30'
    assert dashboard['overview']['actual_qty'] == 150
    assert dashboard['overview']['average_output'] == 3.13
    clock['now'] = datetime(2026, 12, 1, 5, 30, tzinfo=timezone.utc)
    dashboard = client.get('/api/dashboard').get_json()
    assert dashboard['business_date'] == '2026-12-01'
    assert dashboard['overview']['actual_qty'] == 300
    assert dashboard['overview']['average_output'] == 12.5
    clock['now'] = datetime(2027, 1, 1, 5, 30, tzinfo=timezone.utc)
    assert all(value is None for value in client.get('/api/dashboard').get_json()['overview'].values())


def test_screen_displays_monthly_overview(ui_context, live_server, client):
    """验证真实页面；参数为浏览器、临时服务和客户端；断言月标签、累计量与计算结果。"""
    set_standards(client)
    save_overview(client, '2026-11-01', first=50, first_defects=1,
                  oee_percent=90, quality_percent=99, average_output=30)
    save_overview(client, '2026-11-21', first=100, second=200, first_defects=2, second_defects=4,
                  oee_percent=80, quality_percent=97, average_output=60)
    page = ui_context.new_page()
    page.goto(live_server + '/screen')
    expect(page.get_by_text('当月全局指标', exact=True)).to_be_visible()
    expect(page.locator('#plannedQty')).to_have_text('500（件）')
    expect(page.locator('#actualQty')).to_have_text('350（件）')
    expect(page.locator('#completionRate')).to_have_text('70%')
    expect(page.locator('#oeePercent')).to_have_text('—')
    expect(page.locator('#qualityPercent')).to_have_text('98%')
    expect(page.locator('#averageOutput')).to_have_text('4.86（件/时）')
    editor = ui_context.new_page()
    editor.goto(live_server + '/edit')
    expect(editor.get_by_test_id('overview-planned')).to_have_text('400')
    expect(editor.get_by_test_id('overview-actual')).to_have_text('300')
    expect(editor.get_by_test_id('overview-quality')).to_have_text('98%')
    expect(editor.get_by_test_id('overview-average')).to_have_text('6.25')
    editor.get_by_test_id('actual-3').locator('input').fill('150')
    editor.get_by_test_id('actual-3').locator('input').blur()
    expect(editor.get_by_test_id('overview-actual')).to_have_text('350')
    expect(editor.get_by_test_id('overview-average')).to_have_text('7.29')
    editor.get_by_test_id('planned-hours-3').locator('input').fill('')
    editor.get_by_test_id('planned-hours-3').locator('input').blur()
    expect(editor.get_by_test_id('overview-average')).to_have_text('—')
    editor.get_by_test_id('planned-hours-3').locator('input').fill('8')
    editor.get_by_test_id('planned-hours-3').locator('input').blur()
    expect(editor.get_by_test_id('overview-average')).to_have_text('10.94')
    expect(editor.get_by_role('spinbutton', name='平均产出', exact=True)).to_have_count(0)
    expect(editor.get_by_test_id('overview-quality')).to_have_text('98.3%')
    editor.get_by_test_id('defect-3').locator('input').fill('')
    editor.get_by_test_id('defect-3').locator('input').blur()
    expect(editor.get_by_test_id('overview-quality')).to_have_text('—')
    editor.get_by_test_id('defect-3').locator('input').fill('10')
    editor.get_by_test_id('defect-3').locator('input').blur()
    expect(editor.get_by_test_id('overview-quality')).to_have_text('96%')
    expect(editor.get_by_role('spinbutton', name='品质合格率', exact=True)).to_have_count(0)
    expect(editor.get_by_role('spinbutton', name='总计划产量', exact=True)).to_have_count(0)
    editor.get_by_test_id('save-button').click()
    expect(editor.get_by_text('保存成功', exact=True)).to_be_visible()
    page.reload()
    expect(page.locator('#actualQty')).to_have_text('400（件）')
    expect(page.locator('#qualityPercent')).to_have_text('96.3%')
    expect(page.locator('#averageOutput')).to_have_text('7.14（件/时）')
