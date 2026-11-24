from datetime import datetime, timezone

import pytest

from backend.domain import line_runtime


def test_runtime_counts_complete_configured_downtime():
    """验证配置停机口径；无参数；断言当天完整时段参与计算，当前状态仍按实时时刻判断。"""
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    stops = [
        {"start_at": "2026-11-21T09:00:00+00:00", "end_at": "2026-11-21T10:00:00+00:00", "kind": "maintenance"},
        {"start_at": "2026-11-21T11:00:00+00:00", "end_at": "2026-11-21T13:00:00+00:00", "kind": "stopped"},
        {"start_at": "2026-11-21T18:00:00+00:00", "end_at": "2026-11-21T19:00:00+00:00", "kind": "fault"},
    ]
    result = line_runtime(now, "UTC", stops)
    assert result["runtime_rate"] == 83.33
    assert result["downtime_minutes"] == 240
    assert result["status"] == "stopped"


@pytest.mark.parametrize('stops,expected_rate,expected_state', [
    ([], 100, 'running'),
    ([{'start_at': '2026-11-21T18:00:00+00:00', 'end_at': '2026-11-21T19:00:00+00:00', 'kind': 'maintenance'}], 95.83, 'running'),
    ([{'start_at': '2026-11-20T23:00:00+00:00', 'end_at': '2026-11-21T01:00:00+00:00', 'kind': 'stopped'}], 95.83, 'running'),
    ([{'start_at': '2026-11-21T11:00:00+00:00', 'end_at': None, 'kind': 'maintenance'}], 95.83, 'maintenance'),
    ([{'start_at': '2026-11-21T11:00:00+00:00', 'end_at': '2026-11-21T12:00:00+00:00', 'kind': 'fault'}], 95.83, 'running'),
])
def test_runtime_boundaries(stops, expected_rate, expected_state):
    """验证运行率边界；参数为停机样例和独立预期值；断言半开区间与跨天处理。"""
    result = line_runtime(datetime(2026, 11, 21, 12, tzinfo=timezone.utc), 'UTC', stops)
    assert result['runtime_rate'] == expected_rate
    assert result['status'] == expected_state


def test_overlapping_stops_count_union_once():
    """验证重叠时段；无参数；断言九至十二点只扣除三个小时。"""
    stops = [{'start_at': '2026-11-21T09:00:00+00:00', 'end_at': '2026-11-21T11:00:00+00:00', 'kind': 'maintenance'},
             {'start_at': '2026-11-21T10:00:00+00:00', 'end_at': '2026-11-21T12:00:00+00:00', 'kind': 'fault'}]
    result = line_runtime(datetime(2026, 11, 21, 12, tzinfo=timezone.utc), 'UTC', stops)
    assert result['downtime_minutes'] == 180
    assert result['runtime_rate'] == 87.5


@pytest.mark.parametrize('hour', [0, 12, 23])
def test_month_long_stop_covers_whole_business_day(hour):
    """验证整月停机；hour 为当前小时；返回前断言计划二十点四小时的全天停机始终为零运行率。"""
    stops = [{'start_at': '2026-11-01T00:00:00+08:00', 'end_at': '2026-11-30T23:59:00+08:00', 'kind': 'stopped'}]
    now = datetime.fromisoformat(f'2026-11-24T{hour:02d}:00:00+08:00')
    result = line_runtime(now, 'Asia/Shanghai', stops, 20.4)
    assert result['runtime_rate'] == 0
    assert result['downtime_minutes'] == 1440
    assert result['status'] == 'stopped'


def test_configured_future_overlaps_merge_and_clip_at_day_end():
    """验证未来及跨天停机；无参数；断言当天重叠时段合并且不扣除次日部分，当前不提前停机。"""
    stops = [{'start_at': '2026-11-21T18:00:00+00:00', 'end_at': '2026-11-22T04:00:00+00:00', 'kind': 'stopped'},
             {'start_at': '2026-11-21T20:00:00+00:00', 'end_at': '2026-11-21T23:00:00+00:00', 'kind': 'fault'}]
    result = line_runtime(datetime(2026, 11, 21, 12, tzinfo=timezone.utc), 'UTC', stops, 12)
    assert result['runtime_rate'] == 50
    assert result['downtime_minutes'] == 360
    assert result['status'] == 'running'


def test_midnight_has_no_invalid_division():
    """验证午夜固定分母；无参数；断言无停机时按默认二十四小时显示百分之百。"""
    result = line_runtime(datetime(2026, 11, 21, tzinfo=timezone.utc), 'UTC', [])
    assert result['runtime_rate'] == 100
    assert result['status'] == 'running'


def test_business_day_uses_selected_timezone():
    """验证业务时区；无参数；断言停机归属上海业务日，分母仍为默认二十四小时。"""
    stops = [{'start_at': '2026-11-21T00:00:00+00:00', 'end_at': '2026-11-21T01:00:00+00:00', 'kind': 'stopped'}]
    result = line_runtime(datetime(2026, 11, 21, 4, tzinfo=timezone.utc), 'Asia/Shanghai', stops)
    assert result['runtime_rate'] == 95.83


def test_dst_repeated_hour_uses_actual_duration():
    """验证夏令时回拨；无参数；断言重复时段计实际一小时，计划时长保持二十四小时。"""
    stops = [{'start_at': '2026-11-01T01:30:00-04:00', 'end_at': '2026-11-01T01:30:00-05:00', 'kind': 'stopped'}]
    result = line_runtime(datetime(2026, 11, 1, 8, tzinfo=timezone.utc), 'America/Detroit', stops)
    assert result['runtime_rate'] == 95.83


def test_nonexistent_local_time_is_rejected():
    """验证春季跳时；无参数；断言不存在的本地时间不能保存。"""
    from backend.domain import ValidationError, parse_datetime
    with pytest.raises(ValidationError):
        parse_datetime('2026-03-08T02:30:00', 'America/Detroit')
