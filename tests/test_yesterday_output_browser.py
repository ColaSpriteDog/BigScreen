"""验证产线卡片的昨日数量与当日运行状态在桌面、手机上一致。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_api import production_payload


@pytest.mark.parametrize('width,height', [(1440, 720), (375, 812)])
def test_cards_show_yesterday_output_and_defects_with_live_status(ui_context, live_server, client, width, height):
    """验证卡片展示；参数为隔离浏览器、接口及尺寸；断言昨日数值、零空值和实时停机状态。"""
    yesterday = production_payload(client, '2026-11-20')
    for line, actual, defect in zip(yesterday['lines'], [123.45, 0, None, 100], [1.25, 0, None, 2]):
        line.update(actual_qty=actual, defect_qty=defect)
    assert client.put('/api/production/2026-11-20', json=yesterday).status_code == 200
    today = production_payload(client)
    today['lines'][0].update(actual_qty=999, defect_qty=9, planned_hours=12, abnormal_hours=2)

    assert client.put('/api/production/2026-11-21', json=today).status_code == 200
    from tests.test_api import settings_payload
    config = settings_payload(client)
    config['lines'][0]['manual_status'] = 'maintenance'
    assert client.put('/api/settings', json=config).status_code == 200
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_server + '/screen')
    expect(page.locator('#connectionStatus')).to_have_text('数据已同步')
    first = page.locator('.line-card[data-line-id="1"]')
    metrics = first.locator('.data-list > div')
    expect(metrics.nth(1)).to_have_text('昨日产量（米）123.45')
    expect(metrics.nth(3)).to_have_text('昨日报废1.25 米（1%）')
    expect(first).to_have_attribute('data-state', 'maintenance')
    expect(metrics.last).to_contain_text('83.3%')
    second = page.locator('.line-card[data-line-id="2"] .data-list > div')
    expect(second.nth(1)).to_have_text('昨日产量（件）0')
    expect(second.nth(3)).to_have_text('昨日不良0 件')
    third = page.locator('.line-card[data-line-id="3"] .data-list > div')
    expect(third.nth(1)).to_have_text('昨日产量（件）—')
    expect(third.nth(3)).to_have_text('昨日不良—')
    expect(page.locator('.line-card[data-line-id="4"] .data-list > div').nth(3)).to_have_text('昨日不良2 件（2%）')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors, errors
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.screenshot(path=f'output/playwright/yesterday-output-{width}.png', full_page=width < 1201)
