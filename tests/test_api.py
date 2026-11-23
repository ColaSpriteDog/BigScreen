import pytest


def test_new_database_defaults_to_china_standard_time(tmp_path):
    """验证新数据库默认业务时区；tmp_path 为隔离目录；断言首次初始化使用中国标准时间。"""
    from backend import create_app

    app = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'default-zone.sqlite3')})
    with app.test_client() as instance:
        assert instance.get('/api/dashboard').get_json()['timezone'] == 'Asia/Shanghai'


def test_empty_database_has_configuration_but_no_demo_business_data(client):
    """验证首次启动；client 为接口客户端；断言仅有配置且没有模拟业务数量。"""
    response = client.get('/api/dashboard')
    assert response.status_code == 200
    data = response.get_json()
    assert len(data['lines']) == 4
    assert len(data['processes']) == 5
    assert all(line['actual_qty'] is None and line['monthly_qty'] is None for line in data['lines'])
    assert data['inventory']['raw'] == []
    assert data['sales']['shipped_qty'] is None
    assert data['safety_days'] is None
    assert data['trend']['dates'] == [f'2026-11-{day:02d}' for day in range(1, 22)]
    assert len(data['trend']['totals']) == 21
    assert all(len(series['data']) == 21 for series in data['trend']['series'])


def production_payload(client, day='2026-11-21'):
    """构造真实编辑表单；client 为客户端、day 为业务日；返回通过读取接口获得的草稿。"""
    snapshot = client.get('/api/editor', query_string={'date': day}).get_json()
    return {'version': snapshot['version'], **snapshot['production']}


def test_daily_output_drives_monthly_and_trend_and_overview(client):
    """验证日报保存联动；client 为客户端；断言日报、月累计、趋势和全局口径分别正确。"""
    draft = production_payload(client)
    for line, qty, defects in zip(draft['lines'], [1280, 960, 1420, 1360], [18, 22, 15, 38]):
        line.update(actual_qty=qty, defect_qty=defects)
    draft['overview'].update(planned_qty=8600, actual_qty=6010, oee_percent=95, quality_percent=98.6, average_output=45)
    result = client.put('/api/production/2026-11-21', json=draft)
    assert result.status_code == 200
    data = client.get('/api/dashboard').get_json()
    assert data['lines'][0]['monthly_qty'] == 1280
    assert data['lines'][0]['defect_rate'] == 1.41
    assert data['trend']['totals'][-1] == 2780
    assert data['trend']['period_total'] == 2780
    assert data['overview']['actual_qty'] == 6010
    assert data['overview']['completion_rate'] == 69.88


def test_inventory_fields_and_derived_totals_are_independent(client):
    """验证库存保存；client 为客户端；断言小数卷数、独立可切件数、零库存提示和单位分组。"""
    raw = [dict(material='钢卷', warehouse='原材料仓库', specification='1.6*1430', position='A柱',
                quantity=0.5, unit='卷', weight_kg=None, cuttable_qty=123),
           dict(material='钢卷', warehouse='原材料仓库', specification='1.6*1142', position='A柱',
                quantity=0, unit='卷', weight_kg=0, cuttable_qty=0),
           dict(material='板料', warehouse='原材料仓库', specification='2.0', position='',
                quantity=10, unit='张', weight_kg=20.5, cuttable_qty=None)]
    semi = [dict(category='左件', material='门槛左件', warehouse='半成品仓库', specification='1.4', quantity=2977, unit='个')]
    response = client.put('/api/inventory', json={'version': 0, 'raw': raw, 'semi': semi})
    assert response.status_code == 200
    inventory = client.get('/api/dashboard').get_json()['inventory']
    assert inventory['raw_totals'] == [{'quantity': 0.5, 'unit': '卷'}, {'quantity': 10, 'unit': '张'}]
    assert inventory['semi_totals'] == [{'quantity': 2977, 'unit': '件'}]
    assert inventory['zero_count'] == 1
    assert inventory['raw'][0]['cuttable_qty'] == 123
    assert inventory['raw'][0]['weight_kg'] is None


def test_rejected_inventory_batch_rolls_back_everything(client):
    """验证失败回滚；client 为客户端；断言半成品无效时原材料也没有部分保存。"""
    raw = dict(material='钢卷', warehouse='原材料仓库', specification='1.6', position='A柱',
               quantity=5, unit='卷', weight_kg=500, cuttable_qty=100)
    bad_semi = dict(category='左件', material='左件', warehouse='半成品仓库', specification='1.4', quantity=-1, unit='个')
    response = client.put('/api/inventory', json={'version': 0, 'raw': [raw], 'semi': [bad_semi]})
    assert response.status_code == 400
    data = client.get('/api/dashboard').get_json()
    assert data['version'] == 0
    assert data['inventory']['raw'] == []


def test_sales_total_uses_all_parts_and_display_only_top_eight(client):
    """验证销售汇总；client 为客户端；断言完整明细参与累计、界面仅展示前八项。"""
    items = [{'part_no': f'P-{index}', 'quantity': index * 10} for index in range(1, 11)]
    result = client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 1000, 'items': items})
    assert result.status_code == 200
    data = client.get('/api/dashboard').get_json()['sales']
    assert data['shipped_qty'] == 550
    assert data['achievement_rate'] == 55
    assert len(data['items']) == 8
    assert data['items'][0]['part_no'] == 'P-10'
    assert len(client.get('/api/editor').get_json()['sales']['items']) == 10


def test_downtime_automatically_updates_line_and_process(client, clock):
    """验证停机联动；client、clock 为接口与可控时间；断言结束边界自动恢复且不用重新保存。"""
    from datetime import datetime, timezone
    draft = production_payload(client)
    draft['downtimes'] = [{'line_id': 4, 'start_at': '2026-11-21T11:00:00',
                           'end_at': '2026-11-21T13:00:00', 'kind': 'maintenance', 'note': ''}]
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    data = client.get('/api/dashboard').get_json()
    assert data['lines'][3]['status'] == 'maintenance'
    assert data['lines'][3]['runtime_rate'] == 95.83
    assert data['processes'][3]['status_label'] == 'B线维护'
    clock['now'] = datetime(2026, 11, 21, 13, tzinfo=timezone.utc)
    fresh = client.get('/api/dashboard').get_json()
    assert fresh['version'] == data['version']
    assert fresh['lines'][3]['status'] == 'running'
    assert fresh['processes'][3]['status'] == 'running'


def test_stale_save_cannot_overwrite_latest_data(client):
    """验证多人编辑版本保护；client 为客户端；断言旧版本被拒绝且新数据保留。"""
    assert client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 100, 'items': []}).status_code == 200
    result = client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 999, 'items': []})
    assert result.status_code == 409
    assert client.get('/api/dashboard').get_json()['sales']['order_qty'] == 100


def test_invalid_record_id_returns_validation_error_instead_of_server_error(client):
    """验证非法 ID；client 为客户端；断言非标量 ID 被作为输入错误处理。"""
    raw = dict(id=[], material='钢卷', warehouse='原材料仓库', specification='1.6', position='A柱',
               quantity=5, unit='卷', weight_kg=None, cuttable_qty=None)
    assert client.put('/api/inventory', json={'version': 0, 'raw': [raw], 'semi': []}).status_code == 400


def settings_payload(client):
    """构造配置草稿；client 为客户端；返回独立工序与固定产线的保存载荷。"""
    data = client.get('/api/editor').get_json()
    return {'version': data['version'], 'timezone': data['settings']['timezone'], 'safety_start_date': None,
            'lines': data['production']['lines'], 'processes': [item for item in data['processes'] if item['id'] in (1, 5)]}


def test_settings_safety_days_and_persistence_after_restart(client, app, clock):
    """验证设置和持久化；参数为接口、应用和时钟；断言重建应用后数据与配置均保留。"""
    from backend import create_app
    data = settings_payload(client)
    data['safety_start_date'] = '2026-11-01'
    data['lines'][0]['standard_qty'] = 1320
    assert client.put('/api/settings', json=data).status_code == 200
    restarted = create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'], 'BUSINESS_TIMEZONE': 'Asia/Shanghai',
                            'NOW_PROVIDER': lambda: clock['now']}).test_client()
    result = restarted.get('/api/dashboard').get_json()
    assert result['safety_days'] == 20
    assert result['timezone'] == 'UTC'
    assert result['lines'][0]['standard_qty'] == 1320
    assert result['version'] == 1


def test_quick_stop_and_resume_are_versioned_and_idempotent_on_retry(client, clock):
    """验证即时操作；client、clock 为接口及时间；断言重复开始、重复结束均不产生重复记录。"""
    from datetime import timedelta
    started = client.post('/api/downtime/start', json={'version': 0, 'line_id': 4, 'kind': 'maintenance'})
    assert started.status_code == 200
    identifier = started.get_json()['result']
    assert client.post('/api/downtime/start', json={'version': 0, 'line_id': 4, 'kind': 'maintenance'}).status_code == 409
    assert client.post('/api/downtime/start', json={'version': 1, 'line_id': 4, 'kind': 'maintenance'}).status_code == 409
    clock['now'] += timedelta(minutes=10)
    assert client.post(f'/api/downtime/{identifier}/finish', json={'version': 1}).status_code == 200
    assert client.post(f'/api/downtime/{identifier}/finish', json={'version': 2}).status_code == 409
    data = client.get('/api/dashboard').get_json()
    assert data['lines'][3]['status'] == 'running'
    assert data['lines'][3]['runtime_rate'] == 99.31
    assert len(client.get('/api/editor').get_json()['production']['downtimes']) == 1


def test_cross_day_downtime_can_be_edited_from_following_day(client):
    """验证跨天记录；client 为客户端；断言前日开始的记录也可在当日编辑。"""
    draft = production_payload(client, '2026-11-20')
    draft['downtimes'] = [{'line_id': 1, 'start_at': '2026-11-20T23:00:00',
                           'end_at': '2026-11-21T01:00:00', 'kind': 'stopped'}]
    assert client.put('/api/production/2026-11-20', json=draft).status_code == 200
    current = production_payload(client)
    assert len(current['downtimes']) == 1
    current['downtimes'][0]['end_at'] = '2026-11-21T02:00:00'
    assert client.put('/api/production/2026-11-21', json=current).status_code == 200
    assert client.get('/api/dashboard').get_json()['lines'][0]['runtime_rate'] == 91.67


def test_duplicate_parts_roll_back_previous_month_data(client):
    """验证件号去重及回滚；client 为客户端；断言大小写重复件号不会破坏原数据。"""
    assert client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 100, 'items': [{'part_no': 'P-1', 'quantity': 20}]}).status_code == 200
    result = client.put('/api/sales/2026-11', json={'version': 1, 'order_qty': 500,
                        'items': [{'part_no': 'A', 'quantity': 1}, {'part_no': 'a', 'quantity': 2}]})
    assert result.status_code == 400
    sales = client.get('/api/dashboard').get_json()['sales']
    assert sales['order_qty'] == 100
    assert sales['shipped_qty'] == 20


def test_month_and_daily_records_do_not_overwrite_other_periods(client):
    """验证历史周期隔离；client 为客户端；断言昨日累加到本月且上月销售不混入当月。"""
    draft = production_payload(client, '2026-11-20')
    draft['lines'][2].update(actual_qty=50, defect_qty=0)
    assert client.put('/api/production/2026-11-20', json=draft).status_code == 200
    today = production_payload(client)
    today['lines'][2].update(actual_qty=70, defect_qty=0)
    assert client.put('/api/production/2026-11-21', json=today).status_code == 200
    assert client.put('/api/sales/2026-10', json={'version': 2, 'order_qty': 500, 'items': [{'part_no': 'OLD', 'quantity': 100}]}).status_code == 200
    data = client.get('/api/dashboard').get_json()
    assert data['lines'][2]['actual_qty'] == 70
    assert data['lines'][2]['monthly_qty'] == 120
    assert data['trend']['series'][0]['data'][-2:] == [50, 70]
    assert data['sales']['items'] == []


def test_zero_denominators_and_explicit_zero_are_preserved(client):
    """验证零值口径；client 为客户端；断言零数量不丢失、零分母不产生无效百分比。"""
    draft = production_payload(client)
    for item in draft['lines']: item.update(actual_qty=0, defect_qty=0)
    draft['overview'].update(planned_qty=0, actual_qty=0)
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    assert client.put('/api/sales/2026-11', json={'version': 1, 'order_qty': 0, 'items': [{'part_no': 'ZERO', 'quantity': 0}]}).status_code == 200
    data = client.get('/api/dashboard').get_json()
    assert data['lines'][0]['actual_qty'] == 0
    assert data['lines'][0]['defect_rate'] is None
    assert data['trend']['period_total'] == 0
    assert data['sales']['shipped_qty'] == 0
    assert data['sales']['achievement_rate'] is None


def test_invalid_dates_payloads_and_types_return_safe_errors(client):
    """验证接口输入边界；client 为客户端；断言日期、JSON 类型及超限输入均被拒绝。"""
    for path in ['/api/editor?date=2026-02-30', '/api/editor?month=2026-13']:
        assert client.get(path).status_code == 400
    assert client.put('/api/inventory', json=[]).status_code == 400
    assert client.put('/api/inventory', data='wrong', content_type='text/plain').status_code == 415
    assert client.put('/api/inventory', json={'version': True, 'raw': [], 'semi': []}).status_code == 400
    assert client.get('/api/missing').status_code == 404
    invalid = settings_payload(client)
    invalid['timezone'] = 'Missing/Zone'
    assert client.put('/api/settings', json=invalid).status_code == 400
    invalid['timezone'] = 'UTC'
    invalid['safety_start_date'] = '2026-11-22'
    assert client.put('/api/settings', json=invalid).status_code == 400
    assert client.get('/api/dashboard').get_json()['version'] == 0


def test_invalid_timezone_path_and_huge_integer_are_validation_errors(client):
    """验证异常输入类型；client 为客户端；断言非法时区路径及超大整数不会造成服务端异常。"""
    config = settings_payload(client)
    config['timezone'] = '../UTC'
    assert client.put('/api/settings', json=config).status_code == 400
    assert client.put('/api/sales/2026-11', json={'version': 0, 'order_qty': 10 ** 400, 'items': []}).status_code == 400


def test_late_sql_failure_rolls_back_both_inventory_tables(client):
    """验证真正的跨表回滚；client 为客户端；断言第二表约束失败时第一表已执行写入也会撤回。"""
    original = dict(material='钢卷', warehouse='原材料仓库', specification='原规格', position='A柱', quantity=5, unit='卷')
    assert client.put('/api/inventory', json={'version': 0, 'raw': [original], 'semi': []}).status_code == 200
    changed = {**original, 'specification': '新规格', 'quantity': 9}
    duplicate = dict(category='左件', material='门槛', warehouse='半成品仓库', specification='1.4', quantity=2, unit='个')
    response = client.put('/api/inventory', json={'version': 1, 'raw': [changed], 'semi': [duplicate, duplicate]})
    assert response.status_code == 400
    data = client.get('/api/dashboard').get_json()
    assert data['version'] == 1
    assert data['inventory']['raw'][0]['specification'] == '原规格'
    assert data['inventory']['raw'][0]['quantity'] == 5
    assert data['inventory']['semi'] == []


@pytest.mark.parametrize('selection', range(1, 16))
def test_any_nonempty_trend_selection_updates_names_and_totals(client, selection):
    """验证全部非空产线组合；selection 为四位选择掩码；断言名称、曲线与合计使用所选日报。"""
    draft = production_payload(client)
    for line, quantity in zip(draft['lines'], [100, 200, 300, 400]):
        line.update(actual_qty=quantity, defect_qty=0)
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    config = settings_payload(client)
    chosen = []
    for line in config['lines']:
        line['name'] = f'设备{line["id"]}'
        line['show_in_trend'] = bool(selection & (1 << (line['id'] - 1)))
        if line['show_in_trend']: chosen.append(line['id'])
    assert client.put('/api/settings', json=config).status_code == 200
    data = client.get('/api/dashboard').get_json()
    assert [item['line_id'] for item in data['trend']['series']] == chosen
    assert [item['name'] for item in data['trend']['series']] == [f'设备{identifier}' for identifier in chosen]
    assert data['trend']['totals'][-1] == sum(identifier * 100 for identifier in chosen)
    assert data['trend']['period_total'] == sum(identifier * 100 for identifier in chosen)
    assert [line['monthly_qty'] for line in data['lines']] == [100, 200, 300, 400]
    restored = client.get('/api/editor').get_json()['production']['lines']
    assert [line['id'] for line in restored if line['show_in_trend']] == chosen


def test_empty_trend_selection_is_rejected_atomically(client):
    """验证空选择；client 为接口客户端；断言拒绝保存且名称和版本也不被部分修改。"""
    config = settings_payload(client)
    for line in config['lines']: line['show_in_trend'] = False
    config['lines'][0]['name'] = '不应保存的名称'
    response = client.put('/api/settings', json=config)
    assert response.status_code == 400
    assert '至少选择一条产线' in response.get_json()['error']
    data = client.get('/api/dashboard').get_json()
    assert data['version'] == 0
    assert data['lines'][0]['name'] == '激光落料线'
    assert [line['line_id'] for line in data['trend']['series']] == [3, 4]


@pytest.mark.parametrize('invalid', ['yes', None, 2, 1.5, [], {}])
def test_invalid_trend_flag_is_rejected(client, invalid):
    """验证选择字段类型；invalid 为非法值；断言接口拒绝任意隐式真值转换。"""
    config = settings_payload(client)
    config['lines'][0]['show_in_trend'] = invalid
    assert client.put('/api/settings', json=config).status_code == 400


def test_legacy_settings_payload_preserves_existing_trend_selection(client):
    """验证兼容旧配置载荷；client 为接口；断言未提交新字段时保留数据库现有选择。"""
    config = settings_payload(client)
    for line in config['lines']: del line['show_in_trend']
    assert client.put('/api/settings', json=config).status_code == 200
    data = client.get('/api/dashboard').get_json()
    assert [line['line_id'] for line in data['trend']['series']] == [3, 4]


def test_renaming_retains_downtime_relationship_and_updates_alert_name(client):
    """验证改名关联；client 为接口；断言保留固定 ID 的停机、产量和告警使用当前名称。"""
    draft = production_payload(client)
    draft['lines'][3].update(actual_qty=400, defect_qty=0)
    draft['downtimes'] = [{'line_id': 4, 'start_at': '2026-11-21T11:00:00',
                           'end_at': '2026-11-21T13:00:00', 'kind': 'maintenance'}]
    assert client.put('/api/production/2026-11-21', json=draft).status_code == 200
    config = settings_payload(client)
    config['lines'][3]['name'] = '四号焊接设备'
    assert client.put('/api/settings', json=config).status_code == 200
    data = client.get('/api/dashboard').get_json()
    assert data['lines'][3]['name'] == '四号焊接设备'
    assert data['lines'][3]['actual_qty'] == 400
    assert data['lines'][3]['status'] == 'maintenance'
    assert data['processes'][3]['alert_label'] == '四号焊接设备：维护'
    assert data['processes'][3]['status_label'] == '维护'
