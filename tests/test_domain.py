import pytest

from backend.domain import ValidationError, line_availability


@pytest.mark.parametrize('planned,abnormal,expected', [
    (20, 4, 80), (20.4, 20.4, 0), (12, 1, 91.67),
    (24, 0, 100), (24, None, None), (0, 0, None),
])
def test_availability_uses_daily_hours(planned, abnormal, expected):
    """验证日报时间开动率；参数为计划、异常停机和预期值；断言零、空白和小数不依赖当前时刻。"""
    assert line_availability(planned, abnormal) == expected


@pytest.mark.parametrize('abnormal', [-1, 21, True, '4'])
def test_invalid_abnormal_hours_are_rejected(abnormal):
    """验证异常工时边界；abnormal 为非法值；断言负数、超计划及非数值不参与计算。"""
    with pytest.raises(ValidationError):
        line_availability(20, abnormal)


def test_nonexistent_local_time_is_rejected():
    """验证春季跳时；无参数；断言不存在的本地时间不能保存。"""
    from backend.domain import ValidationError, parse_datetime
    with pytest.raises(ValidationError):
        parse_datetime('2026-03-08T02:30:00', 'America/Detroit')
