const puppeteer = require('puppeteer');
const fs = require('fs');

async function takeScreenshot(page, url, index) {
    try {
        console.log(`Navigating to ${url}...`);
        await page.goto(url, { waitUntil: 'networkidle0' });

        const filename = `screenshot-${index}.png`;
        await page.screenshot({ path: filename, fullPage: true });

        console.log(`Screenshot for ${url} saved as ${filename}`);
    } catch (error) {
        console.error(`Error taking screenshot for ${url}:`, error);
    }
}

(async () => {
    const urlsToScreenshot = [
	'http://localhost:8080/?username=fabiobaltieri',
	'http://localhost:8080/?username=kartben',
    ];

    const browser = await puppeteer.launch({
        headless: 'new',
        args: ['--no-sandbox']
    });

    const page = await browser.newPage();

    await page.setViewport({ width: 1600, height: 900 });

    for (const [index, url] of urlsToScreenshot.entries()) {
        await takeScreenshot(page, url, index);
    }

    await browser.close();

    console.log('All screenshots complete!');
})();
