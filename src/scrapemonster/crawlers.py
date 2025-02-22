import asyncio
import json
import re
from collections import deque
from datetime import timedelta
from functools import partial
from itertools import chain
from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, TimeoutError, async_playwright
from prefect import flow, task
from prefect.cache_policies import INPUTS, TASK_SOURCE
from prefect.concurrency.asyncio import rate_limit
from prefect.logging import get_run_logger
from toolz import last, pipe

OUTPUT_DIR = Path(__file__).parent / ".." / ".." / "data"


# https://en.wikipedia.org/wiki/ISBN#ISBN-13_check_digit_calculation
def is_valid_ean13(ean: str) -> bool:
    """Validates if a string is a valid EAN-13 barcode."""
    if not ean.isdigit() or len(ean) != 13:
        return False
    digits = [int(d) for d in ean]
    checksum = (
        10 - (sum(digits[i] * (3 if i % 2 else 1) for i in range(12)) % 10)
    ) % 10
    return checksum == digits[12]


@task(cache_policy=INPUTS, cache_expiration=timedelta(days=1), retries=1)
async def find_category_pages(on_url: str) -> [str]:
    await rate_limit("tops_website")
    logger = get_run_logger()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(on_url)
        logger.info(f"finding product urls from: {on_url}")

        await page.wait_for_selector("div .plp-carousels div .plp-carousel")
        await page.wait_for_timeout(5_000)
        categories = await page.locator(".plp-carousel__link").all()

        category_links = [await link.get_attribute("href") for link in categories]
        await browser.close()
        return category_links


@task(cache_policy=INPUTS, cache_expiration=timedelta(days=1), retries=1)
async def find_product_pages(on_url: str) -> [str]:
    await rate_limit("tops_website")
    logger = get_run_logger()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(on_url)
        logger.info(f"scraping products from: {on_url}")

        # circular buffer
        product_counts = deque([], 3)

        wait_time = 500  # how long to wait between mouse scroll
        total_wait_time = 0  # how long have we waited so far
        max_wait_time = 300_000  # how long should we spend waiting on each page before stopping (5 minutes)

        # initial wait to allow starting items to load
        await page.wait_for_timeout(3_000)

        # find all products currently loaded
        last_product_count = await page.locator(".product-item-inner-wrap").count()

        # stop if 3 iterations result in no additional items or wait quota exceeded
        while (
            product_counts.count(last_product_count) < 3
            and total_wait_time < max_wait_time
        ):
            for _ in range(3):
                await page.mouse.wheel(0, 300)
                await page.wait_for_timeout(wait_time)
                total_wait_time += wait_time

            await page.wait_for_timeout(2_000)

            last_product_count = await page.locator(".product-item-inner-wrap").count()

            product_counts.append(last_product_count)
            total_wait_time += wait_time
            logger.debug(f"found {last_product_count} products")

        products = await page.locator(".product-item-inner-wrap").all()
        links = [await product.get_attribute("href") for product in products]
        await browser.close()
        return links


my_custom_policy = INPUTS + TASK_SOURCE - "browser"


@task(cache_policy=my_custom_policy, cache_expiration=timedelta(days=1), retries=1)
async def extract_product_info(
    on_url: str, browser: Optional[Browser] = None
) -> dict[str, str | float | None]:
    await rate_limit("tops_website")
    logger = get_run_logger()
    logger.info(f"extracting product data from: {on_url}")
    close_browser = False

    # create browser context if one is not passed in
    if browser is None:
        p = await async_playwright().start()
        close_browser = True
        browser = await p.chromium.launch()
    page = await browser.new_page()
    await page.goto(on_url)

    name = await page.locator(
        ".product-Details-name .product-tile__name"
    ).text_content()

    images = [
        await img.get_attribute("src")
        for img in await page.locator(".img-zoom-container img").all()
    ]
    quantity = None
    if re.search(r" \d+[a-zA-Z]{1,2}\.$", name):
        quantity = pipe(
            name,
            partial(re.split, r"\s+"),
            last,
            partial(re.sub, r"[^a-zA-Z0-9]", ""),
        )
        name = pipe(
            name,
            partial(re.split, r"\s+"),
            lambda x: x[:-1],
            partial(str.join, " "),
            partial(re.sub, r"^\(.+\)\s*", ""),
        )

    sku = await page.locator(".product-Details-sku").text_content()
    sku = pipe(
        sku,
        str.strip,
        partial(re.split, r"\s+"),
        last,
    )
    barcode = f"EAN-13 {sku}" if is_valid_ean13(sku) else None

    try:
        details = await page.locator(
            ".accordion-item-product-details .accordion-body"
        ).text_content(timeout=100)
        details = pipe(
            details,
            partial(re.sub, r"[ \t]+", " "),
            str.strip,
        )
    except TimeoutError:
        details = None

    price = await page.locator(".product-Details-current-price").text_content()
    price = float(price)

    labels = [
        await img.get_attribute("alt")
        for img in await page.locator(
            ".product-Details-common-description img:not(.product-Details-ui.image)"
        ).all()
    ]
    labels = pipe(
        labels,
        partial(map, str.strip),
        partial(filter, bool),
        list,
    )
    data = {
        "name": name,
        "quantity": quantity,
        "price": price,
        "images": images,
        "barcode": barcode,
        "labels": labels,
        "store_url": on_url,
    }
    await page.close()
    if close_browser:
        await browser.close()
    return data


@flow
async def get_products(product_links: [str], output_file="products.jsonl") -> None:
    """Extract data from multiple pages and save in single file."""
    logger = get_run_logger()
    logger.info(f"attempting to extract product info from {len(product_links)} pages")
    products = extract_product_info.map(product_links).result()
    write_results(output_file, products)
    return


@task(log_prints=True)
def write_results(filename, objs: [dict]) -> None:
    """Save results in JSONL format."""
    logger = get_run_logger()
    output = OUTPUT_DIR.resolve()
    output.mkdir(exist_ok=True)
    count = 0
    with open(output / filename, "w") as fp:
        for item in objs:
            # pprint(item)
            fp.write(json.dumps(item) + "\n")
            count += 1
    logger.info(f"wrote {count} records to {output / filename}")
    return


@flow(log_prints=True)
async def main():
    """Crawl entire website."""
    logger = get_run_logger()
    nav_links = [
        "https://www.tops.co.th/en/campaign/promotion-otop-16jan-28feb-2025.html",
        "https://www.tops.co.th/en/campaign/only-at-tops.html",
        "https://www.tops.co.th/en/fruit-and-vegetables",
        "https://www.tops.co.th/en/meat-and-seafood",
        "https://www.tops.co.th/en/fresh-food-bakery",
        "https://www.tops.co.th/en/pantry-and-ingredients",
        "https://www.tops.co.th/en/snacks-and-desserts",
        "https://www.tops.co.th/en/beverages",
        "https://www.tops.co.th/en/health-and-beauty-care",
        "https://www.tops.co.th/en/mom-and-kids",
        "https://www.tops.co.th/en/household-and-pet",
        "https://www.tops.co.th/en/petnme",
    ]
    category_pages = find_category_pages.map(nav_links).result()
    product_listing_pages = list(chain.from_iterable(category_pages))
    logger.info(f"found {len(product_listing_pages)} product listing pages")

    products_pages = find_product_pages.map(product_listing_pages).result()
    product_page_links = list(chain.from_iterable(products_pages))

    await get_products(product_page_links)


@flow(log_prints=True)
async def alt_main():
    """Run crawler small subset of links."""
    nav_links = [
        "https://www.tops.co.th/en/petnme",
    ]
    category_pages = find_category_pages.map(nav_links).result()
    product_listing_pages = list(chain.from_iterable(category_pages))

    products_pages = find_product_pages.map(product_listing_pages[:1]).result()
    product_page_links = list(chain.from_iterable(products_pages))

    await get_products(product_page_links[:10])


if __name__ == "__main__":
    asyncio.run(alt_main())
