// Invoke with a Playwright page and {html, css, js} from the real Jinja macro/assets.
async (page, fixture) => {
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const load = async (target) => {
    await target.goto('about:blank');
    await target.setContent(fixture.html);
    await target.addStyleTag({content: fixture.css});
    await target.addScriptTag({content: fixture.js});
    await target.evaluate(() => document.dispatchEvent(new Event('DOMContentLoaded')));
  };
  const bounds = async (target) => target.locator('.column-tooltip.is-open').evaluate(tip => {
    const r = tip.getBoundingClientRect();
    return {left:r.left, right:r.right, top:r.top, bottom:r.bottom, width:innerWidth, height:innerHeight,
      hit:tip.contains(document.elementFromPoint(r.left+10, r.top+10))};
  });
  const results = [];
  for (const width of [320, 390, 1280]) {
    await page.setViewportSize({width, height:844});
    await load(page);
    const heading = page.locator('thead th').last().locator('.column-heading');
    await heading.hover();
    await page.waitForSelector('.column-tooltip.is-open');
    const r = await bounds(page);
    check(r.left >= 0 && r.right <= r.width && r.bottom <= r.height && r.hit, `Clipped tooltip at ${width}px`);
    await page.locator('.column-tooltip.is-open').hover();
    await page.waitForTimeout(200);
    check(await page.locator('.column-tooltip.is-open').count() === 1, 'Tooltip closes when hovered');
    await page.keyboard.press('Escape');
    check(await page.locator('.column-tooltip.is-open').count() === 0, 'Escape failed');
    await heading.focus();
    await page.waitForSelector('.column-tooltip.is-open');
    check(await heading.getAttribute('aria-describedby'), 'Missing accessible description');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    check(await page.locator('.column-tooltip.is-open').count() === 0, 'Escape must dismiss a focused tooltip');
    await heading.press('Enter');
    await page.waitForSelector('.column-tooltip.is-open');
    await page.locator('#outside').click();
    check(await page.locator('.column-tooltip.is-open').count() === 0, 'Outside click failed');
    const sort = page.locator('.sort-button');
    await sort.click();
    check((await page.locator('.sortable-table tbody tr td:first-child').allTextContents()).join(',') === 'Two,Three,One', 'Ascending sort failed');
    await sort.click();
    check((await page.locator('.sortable-table tbody tr td:first-child').allTextContents()).join(',') === 'One,Three,Two', 'Descending sort failed');
    check(await sort.locator('[tabindex]').count() === 0, 'Nested tab target in sort button');
    const bottom = page.locator('.comparison .column-heading');
    await bottom.hover();
    const bottomBounds = await bounds(page);
    check(bottomBounds.top >= 0 && bottomBounds.bottom <= bottomBounds.height && bottomBounds.hit, 'Bottom tooltip clipped');
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForFunction(() => !document.querySelector('.column-tooltip.is-open'));
    results.push(`${width}px: hover, bounds, keyboard, dismissal, sorting, scrolling passed`);
  }
  const touchContext = await page.context().browser().newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
  try {
    const touchPage = await touchContext.newPage();
    await load(touchPage);
    await touchPage.locator('thead .column-heading').first().tap();
    await touchPage.waitForSelector('.column-tooltip.is-open');
    await touchPage.locator('#outside').tap();
    check(await touchPage.locator('.column-tooltip.is-open').count() === 0, 'Outside tap failed');
    results.push('Touch: tap to open, tap outside to dismiss passed');
  } finally {
    await touchContext.close();
  }
  return results;
}
