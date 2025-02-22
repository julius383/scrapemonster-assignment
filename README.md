# ScrapeMonster.tech Web Scraping Assignment

Scrape product data from https://www.tops.co.th/en across various categories.


## Approach

Since the website makes heavy use of JavaScript to load data dynamically, the
primary approach made use of a headless browser through the [playwright](https://playwright.dev)
library.

The first stage involved opening the website in a browser and navigating
through various sections of the website. Afterwards I used cURL to download
pages through a simple GET request. It was at this point that I realized that
sections were missing from some pages that were present in the browser version.
One particular example is that the price is absent from product pages that were
fetched outside a JavaScript context i.e with cURL.

After the initial exploration I determined that the scrape would need to
proceed in 3 steps:

1. Scrape the product category pages to find sub-category pages where the
   actual products are listed.
(`src/scrapemonster/crawlers.py:find_category_pages`)
1. Scrape the sub-category pages to find the links to individual products.
   Products on these pages are loaded dynamically as a user scrolls.(`src/scrapemonster/crawlers.py:find_product_pages`)
1. Scrape individual product pages for the actual product data. (`src/scrapemonster/crawlers.py:extract_product_info`)

The first step required waiting for the right DOM element to be loaded then
using CSS selectors to extract a URL from a specific part.


The second step is more involved due to the dynamic loading. I use an infinite
loop with two guards to incrementally scroll until all items are loaded.

- The first guard exits the loop when the number of products remains constant
  for 3 consecutive iterations. The exact values used for scrolling and waiting
  were determined through experimentation.
- The second guard puts a fixed cap on the total amount of time we spend trying
  to scrape a page.


The third step involved finding the correct CSS selector for the various
data elements we are interested in. It also does some additional processing on
the individual fields including extracting the quantity and verifying the EAN
barcode number.


## How to Run

### Python
Python dependency management is handled by [uv](https://docs.astral.sh/uv/). Install dependencies in a virtualenv
using 

```sh
uv sync
```

Alternatively using pip:

```sh
pip install --requirement requirements.txt
```

### Playwright


Playwright is also required. Install using `npm`

```sh
npm install -g playwright
```

Then install the Chromium browser

```sh
playwright install chromium
```

[Prefect](https://www.prefect.io/) is used for workflow orchestration. In this project specifically it
is used to handle retries, rate limiting and results caching. Start the prefect
server with

```sh
prefect server start
```

Then run the crawler with:

```sh
python src/scrapemonster/crawlers.py
# or
uv run src/scrapemonster/crawlers.py
```

This runs `alt_main` which runs a simpler version of the crawler. Switch to
`main` to run the crawler on the whole website.

## Challenges

The primary challenge encountered was the extensive employment of dynamic
loading which necessitated the use of a headless browser. This results in
the code for scraping being more complicated and the crawler itself
requiring more system resources to run successfully.

The website does not seem to employ rate limiting but I nonetheless used
`prefect`'s [global concurrency limits](https://docs.prefect.io/v3/develop/global-concurrency-limits) to ensure that the website is not placed
under too much strain.

## Output

Samples from limited runs of the crawler can be seen in the `data/` directory.
The following is a random sample:

```json
[
  {
    "name": "Cesar Chicken And Cheese Flavor",
    "quantity": "100g",
    "price": 51.0,
    "images": [
      "https://assets.tops.co.th/CESAR-CesarChickenAndCheeseFlavor100g-0000093468015-1",
      "https://assets.tops.co.th/CESAR-CesarChickenAndCheeseFlavor100g-0000093468015-2"
    ],
    "barcode": "EAN-13 0000093468015",
    "labels": [],
    "store_url": "https://www.tops.co.th/en/cesar-chicken-and-cheese-flavor-100g-0000093468015"
  },
  {
    "name": "TGM Chaili Arabiki Sausage",
    "quantity": "100g",
    "price": 99.0,
    "images": [
      "https://assets.tops.co.th/TGM-TGMChailiArabikiSausage100g-8850826508264-1?$JPEG$",
      "https://assets.tops.co.th/TGM-TGMChailiArabikiSausage100g-8850826508264-2?$JPEG$"
    ],
    "barcode": "EAN-13 8850826508264",
    "labels": [],
    "store_url": "https://www.tops.co.th/en/tgm-chaili-arabiki-sausage-100g-8850826508264"
  },
  {
    "name": "TGM Cottage Bacon",
    "quantity": "150g",
    "price": 179.0,
    "images": [
      "https://assets.tops.co.th/TGM-TGMBacon150g-8850826507175-1?$JPEG$"
    ],
    "barcode": "EAN-13 8850826507175",
    "labels": [
      "Best Seller"
    ],
    "store_url": "https://www.tops.co.th/en/tgm-cottage-bacon-150g-8850826507175"
  },
  {
    "name": "Cesar Beef Flavour With Vegetables In Gravy",
    "quantity": "70g",
    "price": 22.0,
    "images": [
      "https://assets.tops.co.th/CESAR-CesarBeefFlavourWithVegetablesInGravy70g-4902397839217-1"
    ],
    "barcode": "EAN-13 4902397839217",
    "labels": [],
    "store_url": "https://www.tops.co.th/en/cesar-beef-flavour-with-vegetables-in-gravy-70g-4902397839217"
  },
  {
    "name": "Pedigree Pouch Liver Chick",
    "quantity": "130g",
    "price": 24.0,
    "images": [
      "https://assets.tops.co.th/PEDIGREE-PedigreePouchLiverChick130g-8853301530040-1",
      "https://assets.tops.co.th/PEDIGREE-PedigreePouchLiverChick130g-8853301530040-2"
    ],
    "barcode": "EAN-13 8853301530040",
    "labels": [],
    "store_url": "https://www.tops.co.th/en/pedigree-pouch-liver-chick-130g-8853301530040"
  }
]
```
