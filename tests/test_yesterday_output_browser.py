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
    expect(metrics.nth(1)).to_have_text('昨日产量（米）19,606')
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
    """验证现场长数值；参数为浏览器、服务、客户端及字体；断言米数截去小数、比例保留且字号不缩小。"""
    dashboard = client.get('/api/dashboard').get_json()
    dashboard['lines'][0].update(standard_qty=20000.99, yesterday_qty=20530.99,
                                 monthly_qty=199602.02, yesterday_defect_qty=20.76,
                                 yesterday_defect_rate=0.1)
    # 其他产线继续展示小数。
    dashboard['lines'][1].update(yesterday_qty=1240.75)
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
    expect(first.locator('.val').nth(0)).to_have_text('20,000')
    expect(first.locator('.val').nth(1)).to_have_text('20,530')
    expect(first.locator('.val').nth(2)).to_have_text('199,602')
    expect(first.locator('.val').nth(3)).to_have_text('20.76 米（0.10%）')
    expect(page.locator('.line-card[data-line-id="2"] .val').nth(1)).to_have_text('1,240.75')
    # 在现场分辨率、宽屏和手机间往返，验证字号始终使用样式表默认值。
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
        # 所有卡片都不再通过内联字号缩小数值，昨日产量保留独立强调层级。
        sizes = page.locator('#lineCards .data-list > div').evaluate_all("""rows => {
          const groups = { regular: [], yesterday: [] };
          rows.forEach(row => {
            const group = row === row.parentElement.children[1] ? 'yesterday' : 'regular';
            groups[group].push(getComputedStyle(row.querySelector('.val')).fontSize);
          });
          return groups;
        }""")
        assert len(set(sizes['regular'])) == 1, sizes
        assert len(set(sizes['yesterday'])) == 1, sizes
        assert sizes['regular'][0] == ('14px' if width == 1920 else '13px' if width == 375 else '12px')
        assert sizes['yesterday'][0] == ('20px' if width == 1920 else '16px')
        assert page.locator('#lineCards .val').evaluate_all('(values) => values.every(value => !value.style.fontSize)')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if width == 1440:
            tops = page.locator('.line-card').evaluate_all('(cards) => cards.map(card => card.getBoundingClientRect().top)')
            assert max(tops) - min(tops) < 1
        if width == 1920:
            assert first.locator('.val').nth(1).evaluate('(value) => getComputedStyle(value).fontSize') == '20px'
            # 从窄屏恢复到宽屏后，列对齐也必须恢复，覆盖不同数字字体。
            page.wait_for_function("""() => {
              const rows = document.querySelector('.line-card[data-line-id="1"] .data-list').children;
              return Math.abs(rows[1].getBoundingClientRect().left - rows[3].getBoundingClientRect().left) < 0.5
                && Math.abs(rows[0].querySelector('.val').getBoundingClientRect().right
                  - rows[2].querySelector('.val').getBoundingClientRect().right) < 0.5;
            }""", timeout=3000)
    assert not errors, errors
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.screenshot(path=f'output/playwright/line-metrics-fit-{font or "default"}-1440.png')


@pytest.mark.parametrize('width,height,label_size', [(1440, 720, None), (1366, 768, None),
                                                    (1414, 900, 14), (1440, 900, 14),
                                                    (1920, 1080, None), (2560, 1440, None),
                                                    (1024, 768, None), (375, 812, None)])
def test_cutting_standard_quantity_is_not_clipped(ui_context, live_server, client, width, height, label_size):
    """验证落料线标准产量；参数为隔离环境、视口及标签字号；断言现场数据完整可见、字段不重叠且字号未缩小。"""
    dashboard = client.get('/api/dashboard').get_json()
    dashboard['lines'][0].update(standard_qty=18000, yesterday_qty=19606.02,
                                 monthly_qty=423297.84, yesterday_defect_qty=10.5,
                                 yesterday_defect_rate=0.05, runtime_rate=None)
    # 四条产线同时填入现场数据，防止只验证落料线而漏掉其他卡片的挤压和截断。
    for line, standard, yesterday, monthly, defect, rate, runtime in zip(
            dashboard['lines'][1:], [1420, 1000, 500], [1240, 1066, None],
            [33172, 25512, 872], [0, 3, None], [0, 0.28, None], [90.32, 98.35, 100]):
        line.update(standard_qty=standard, yesterday_qty=yesterday, monthly_qty=monthly,
                    yesterday_defect_qty=defect, yesterday_defect_rate=rate, runtime_rate=runtime)
    page = ui_context.new_page()
    page.set_viewport_size({'width': width, 'height': height})
    page.route('**/api/dashboard', lambda route: route.fulfill(json=dashboard))
    page.goto(live_server + '/screen')
    expect(page.locator('#connectionStatus')).to_have_text('数据已同步')
    if label_size:
        # 复现截图中较大的标签文字，验证列宽能随内容调整。
        page.add_style_tag(content=f'.line-card .data-list {{ font-size: {label_size}px; }}')
    card = page.locator('.line-card[data-line-id="1"]')
    expect(card.locator('.val').first).to_have_text('18,000')
    expect(card.locator('.val').nth(3)).to_have_text('10.5 米（0.05%）')
    # 宽卡片的两行共用左右列，标签起点和数值右边缘都必须对齐。
    columns = card.locator('.data-list').evaluate("""list => {
      const fields = [...list.children].slice(0, 4).map(row => {
        const box = row.getBoundingClientRect();
        const value = row.querySelector('.val').getBoundingClientRect();
        return { left: box.left, right: value.right };
      });
      return { fields, width: list.clientWidth, font: parseFloat(getComputedStyle(list).fontSize) };
    }""")
    if columns['width'] >= columns['font'] * 26:
        for top, bottom in [(0, 2), (1, 3)]:
            assert abs(columns['fields'][top]['left'] - columns['fields'][bottom]['left']) < 0.5, columns
            assert abs(columns['fields'][top]['right'] - columns['fields'][bottom]['right']) < 0.5, columns
    cards = page.locator('#lineCards .line-card').evaluate_all("""cards => cards.map(card => {
      const box = card.getBoundingClientRect();
      return { width: box.width, height: box.height, top: box.top };
    })""")
    assert max(item['width'] for item in cards) - min(item['width'] for item in cards) < 1, cards
    if width > 1200:
        assert max(item['height'] for item in cards) - min(item['height'] for item in cards) < 1, cards
        assert max(item['top'] for item in cards) - min(item['top'] for item in cards) < 1, cards
    bounds = page.locator('#lineCards .data-list > div').evaluate_all("""rows => rows.map(row => {
      const value = row.querySelector('.val');
      const range = document.createRange();
      range.selectNodeContents(value);
      const text = range.getBoundingClientRect();
      const box = value.getBoundingClientRect();
      const field = row.getBoundingClientRect();
      return { content: value.textContent, text: text.width, available: box.width, right: box.right, fieldRight: field.right,
        left: field.left, fontSize: value.style.fontSize };
    })""")
    assert not [item for item in bounds if item['text'] > item['available'] + 0.1]
    assert all(item['right'] <= item['fieldRight'] + 0.1 and not item['fontSize'] for item in bounds), bounds
    for index in range(0, len(bounds), 5):
        assert bounds[index]['right'] + 3 <= bounds[index + 1]['left'], bounds
        assert bounds[index + 2]['right'] + 3 <= bounds[index + 3]['left'], bounds
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    Path('output/playwright').mkdir(parents=True, exist_ok=True)
    page.screenshot(path=f'output/playwright/cutting-standard-{width}-{height}.png', full_page=width < 1201)
