"""验证产线卡片的昨日数量与当日运行状态在桌面、手机上一致。"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.test_api import production_payload


@pytest.mark.parametrize('width,height', [(1440, 720), (375, 812)])
def test_cards_show_yesterday_output_and_defects_with_live_status(ui_context, live_server, client, width, height):
    """验证卡片展示；参数为隔离浏览器、接口及尺寸；断言昨日数值、零空值和实时停机状态。"""
    yesterday = production_payload(client, '2026-11-20')
    for line, actual, defect in zip(yesterday['lines'], [19606.02, 0, None, 100], [4.86, 0, None, 2]):
        line.update(actual_qty=actual, defect_qty=defect)
    # 补全昨日工时，使月累计运行率断言不受历史缺失工时影响。
    yesterday['lines'][0].update(planned_hours=12, abnormal_hours=2)
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
    expect(metrics.nth(1)).to_have_text('昨日产量（米）19,606.02')
    expect(metrics.nth(3)).to_have_text('昨日报废4.86 米（0.02%）')
    expect(first).to_have_attribute('data-state', 'maintenance')
    expect(metrics.last).to_contain_text('83.33%')
    second = page.locator('.line-card[data-line-id="2"] .data-list > div')
    expect(second.nth(1)).to_have_text('昨日产量（件）0')
    expect(second.nth(3)).to_have_text('昨日不良0 件')
    third = page.locator('.line-card[data-line-id="3"] .data-list > div')
    expect(third.nth(1)).to_have_text('昨日产量（件）—')
    expect(third.nth(3)).to_have_text('昨日不良—')
    expect(page.locator('.line-card[data-line-id="4"] .data-list > div').nth(3)).to_have_text('昨日不良2 件（2.00%）')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors, errors
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.screenshot(path=f'output/playwright/yesterday-output-{width}.png', full_page=width < 1201)


@pytest.mark.parametrize('font', [None, 'Consolas', 'monospace'])
def test_line_metrics_remain_visible_with_decimal_values(ui_context, live_server, client, font):
    """验证现场长数值；参数为浏览器、服务、客户端及字体；断言小数和比例完整，缩放后仍保持原布局。"""
    dashboard = client.get('/api/dashboard').get_json()
    dashboard['lines'][0].update(standard_qty=20000.44, yesterday_qty=20530.44,
                                 monthly_qty=424222.22, yesterday_defect_qty=20.76,
                                 yesterday_defect_rate=0.1)
    page = ui_context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/api/dashboard', lambda route: route.fulfill(json=dashboard))
    if font:
        stylesheet = Path('static/css/screen.css').read_text(encoding='utf-8')
        page.route('**/static/css/screen.css?*', lambda route: route.fulfill(
            body=stylesheet + f'\n:root {{ --font-number: {font}; }}', content_type='text/css'))
    page.goto(live_server + '/screen')
    expect(page.locator('#connectionStatus')).to_have_text('数据已同步')
    first = page.locator('.line-card[data-line-id="1"]')
    expect(first.locator('.val').nth(1)).to_have_text('20,530.44')
    expect(first.locator('.val').nth(3)).to_have_text('20.76 米（0.10%）')
    # 在现场分辨率、宽屏和手机间往返，验证重新测量和恢复字号，而不只核对 DOM 文本。
    for width, height in [(1440, 720), (1920, 1080), (375, 812), (1440, 720)]:
        page.set_viewport_size({'width': width, 'height': height})
        page.wait_for_function("""() => [...document.querySelectorAll('#lineCards .val')].every(value => {
          const range = document.createRange();
          range.selectNodeContents(value);
          return range.getBoundingClientRect().width <= value.getBoundingClientRect().width
            && parseFloat(getComputedStyle(value).fontSize) >= 10;
        })
        """, timeout=3000)
        layout = first.evaluate("""card => [...card.querySelectorAll('.data-list > div')].map(row => {
          const label = document.createRange();
          label.selectNodeContents(row.firstChild);
          const number = row.querySelector('.val').getBoundingClientRect();
          const text = label.getBoundingClientRect();
          return { gap: number.left - text.right, overlap: number.top < text.bottom && text.top < number.bottom };
        })""")
        assert all(item['gap'] >= 3 and item['overlap'] for item in layout), layout
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if width == 1440:
            tops = page.locator('.line-card').evaluate_all('(cards) => cards.map(card => card.getBoundingClientRect().top)')
            assert max(tops) - min(tops) < 1
        if width == 1920:
            assert first.locator('.val').nth(1).evaluate('(value) => getComputedStyle(value).fontSize') == '20px'
    assert not errors, errors
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.screenshot(path=f'output/playwright/line-metrics-fit-{font or "default"}-1440.png')
