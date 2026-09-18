import os
import sys
import json
import pandas as pd
import logging
import time
from datetime import datetime
import pytz
from py_clob_client.client import ClobClient
from strategies import trades
from get_order_book import update_books_for_trades  # Import the function
from dotenv import load_dotenv
import numpy as np
from get_live_price import get_live_price  # Import the new live price function
import jinja2
import tempfile
import numpy as np
import subprocess

# Access the environment variables
api_key = os.getenv('API_KEY')

# Set up logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Replace with your actual host and chain ID
host = "https://clob.polymarket.com"
chain_id = 137  # Polygon Mainnet

# Initialize the ClobClient
client = ClobClient(host, key=api_key, chain_id=chain_id)

# Dictionary to cache live prices
live_price_cache = {}

# Load environment variables
load_dotenv()

def load_market_lookup():
    with open('./data/market_lookup.json', 'r') as f:
        market_lookup = json.load(f)

    slug_to_token_id = {}
    for market in market_lookup.values():
        slug = market['market_slug']
        slug_to_token_id[slug] = {token['outcome']: token['token_id'] for token in market['tokens']}

    return slug_to_token_id

def get_actual_price(slug, outcome, user_id='JeremyRWhittaker'):
    """
    Get the actual price and size from the user's latest trade for the specified slug and outcome.
    """
    file_path = f'./data/user_trades/{user_id}_enriched_transactions.parquet'
    if not os.path.exists(file_path):
        logging.warning(f"User trades file not found: {file_path}")
        return None, None
    try:
        df = pd.read_parquet(file_path)
    except Exception as e:
        logging.error(f"Failed to read user trades file: {file_path}. Error: {e}")
        return None, None

    # Parse 'timeStamp_erc1155' as datetime
    try:
        df['timeStamp_erc1155'] = pd.to_datetime(df['timeStamp_erc1155'])
    except Exception as e:
        logging.error(f"Failed to parse 'timeStamp_erc1155' as datetime: {e}")
        return None, None

    # Filter by market_slug and outcome
    df_filtered = df[(df['market_slug'] == slug) & (df['outcome'] == outcome)]
    if df_filtered.empty:
        logging.info(f"No trades found for user {user_id} in market {slug} ({outcome})")
        return None, None

    # Get the row with the latest 'timeStamp_erc1155'
    latest_trade = df_filtered.loc[df_filtered['timeStamp_erc1155'].idxmax()]
    price = latest_trade['price_paid_per_token']
    size = latest_trade['shares']

    return price, size

def get_price_and_size(df, price_type):
    if price_type == 'ask':
        relevant_df = df[df['side'] == 'ask']
        if not relevant_df.empty:
            min_price_row = relevant_df.loc[relevant_df['price'].idxmin()]
            return min_price_row['price'], min_price_row['size']
    elif price_type == 'bid':
        relevant_df = df[df['side'] == 'bid']
        if not relevant_df.empty:
            max_price_row = relevant_df.loc[relevant_df['price'].idxmax()]
            return max_price_row['price'], max_price_row['size']
    elif price_type == 'mid':
        ask_df = df[df['side'] == 'ask']
        bid_df = df[df['side'] == 'bid']
        if not ask_df.empty and not bid_df.empty:
            min_ask_price = ask_df['price'].min()
            max_bid_price = bid_df['price'].max()
            return (min_ask_price + max_bid_price) / 2, None
    return None, None

def get_live_price(token_id, side):
    cache_key = f"{token_id}_{side.upper()}"
    current_time = time.time()  # Get the current time in seconds since the Epoch

    # Check if the price is in the cache and if it's still valid (not older than 2 minutes)
    if cache_key in live_price_cache:
        cached_price, timestamp = live_price_cache[cache_key]
        if current_time - timestamp < 60:
            return cached_price
        else:
            logging.info(f"Cache expired for {cache_key}. Fetching a new price.")

    try:
        response = client.get_last_trade_price(token_id=token_id)
        price = response.get('price')
        live_price_cache[cache_key] = (price, current_time)  # Store the price with the current timestamp
        return price

    except Exception as e:
        logging.error(f"Failed to fetch live price for token {token_id} on side {side}: {str(e)}")
        return None
def get_live_price_from_file(token_id, side):
    """
    Use the external program to fetch live prices.
    """
    price = get_live_price(token_id, side)
    if price is not None:
        return price
    else:
        logging.warning(f"Failed to get live price for token ID {token_id}")
        return None
def save_trade_details_with_prices(trade, trade_side_keys, price_type, output_dir, slug_to_token_id, user_id='JeremyRWhittaker'):
    trade_name = trade['trade_name']
    price_type_suffix = f"_{price_type}"

    trade_data = []

    for trade_side_key in trade_side_keys:
        trade_side = trade.get(trade_side_key, [])

        for slug, outcome in trade_side:
            token_id = slug_to_token_id.get(slug, {}).get(outcome)
            if not token_id:
                logging.warning(f"Token ID not found for {slug} ({outcome})")
                continue

            if price_type == 'actual':
                price, size = get_actual_price(slug, outcome, user_id)
                if price is None:
                    logging.warning(f"Actual price not found for {slug} ({outcome}), skipping this pair.")
                    continue
            elif price_type == 'live':
                price = get_live_price_from_file(token_id, side='sell' if outcome.lower() == 'no' else 'buy')
                size = None  # We don't have size data for live prices
            else:
                file_name = f"{slug}_{outcome}.csv"
                file_path = os.path.join('./data/book_data', file_name)
                if not os.path.exists(file_path):
                    logging.info(f"File not found: {file_path}")
                    continue
                df = pd.read_csv(file_path)
                price, size = get_price_and_size(df, price_type)

            if price is not None:
                trade_data.append({
                    'Slug': f"{slug} ({outcome})",
                    'Side': trade_side_key,
                    'Price': float(price),  # Ensure price is a float
                    'Size': size
                })

    if trade_data:
        df_trade = pd.DataFrame(trade_data)
        output_path = os.path.join(output_dir, f"{trade_name}{price_type_suffix}.csv")
        df_trade.to_csv(output_path, index=False)
        logging.info("Saved detailed trade information for %s to %s", trade_name, output_path)

def calculate_arbitrage_for_scenarios(trades, data_dir='./data/book_data', price_types=['ask', 'mid', 'live', 'bid', 'actual'], user_id='JeremyRWhittaker'):
    """
    Calculate arbitrage for different price types, including 'bid' and 'actual'.
    """
    arbitrage_info = {}
    slug_to_token_id = load_market_lookup()

    for trade in trades:
        trade_name = trade['trade_name']
        method = trade.get("method")

        arbitrage_per_price_type = {}

        if method == 'all_no':
            positions = trade.get('positions', [])
            if not positions:
                logging.info(f"No positions found for trade: {trade_name}")
                continue
        elif method == 'balanced':
            side_a_positions = trade.get('side_a_trades', [])
            side_b_positions = trade.get('side_b_trades', [])
            if not side_a_positions or not side_b_positions:
                logging.info(f"No positions found for trade: {trade_name}")
                continue
        else:
            continue

        for price_type in price_types:
            data_complete = True  # Flag to check if all required data is available

            if method == 'all_no':
                # For 'all_no' method
                prices = []
                for slug, outcome in positions:
                    logging.info(f"Processing strategy: {trade_name} using {price_type} prices")

                    token_id = slug_to_token_id.get(slug, {}).get(outcome)
                    if not token_id:
                        logging.warning(f"Token ID not found for {slug} ({outcome}), skipping this pair.")
                        data_complete = False
                        break

                    if price_type == 'actual':
                        price, size = get_actual_price(slug, outcome, user_id)
                        if price is None:
                            logging.warning(f"Actual price not found for {slug} ({outcome}), skipping this pair.")
                            data_complete = False
                            break
                    elif price_type == 'live':
                        price = get_live_price(token_id, side='sell' if outcome.lower() == 'no' else 'buy')
                    else:
                        file_name = f"{slug}_{outcome}.csv"
                        file_path = os.path.join(data_dir, file_name)
                        if not os.path.exists(file_path):
                            logging.info(f"File not found: {file_path}, skipping this pair.")
                            data_complete = False
                            break
                        df = pd.read_csv(file_path)
                        price, _ = get_price_and_size(df, price_type)
                        if price is None:
                            logging.warning(f"Price not found in file for {slug} ({outcome}), skipping this pair.")
                            data_complete = False
                            break

                    try:
                        price = float(price)
                    except (TypeError, ValueError) as e:
                        logging.error(f"Failed to convert price to float for {slug} ({outcome}): {e}")
                        data_complete = False
                        break

                    prices.append(price)

                if data_complete and prices:
                    max_price = max(prices)
                    total_winnings = sum(1 - p for p in prices) - (1 - max_price)
                    arb_pct = (total_winnings - max_price) * 100
                    logging.info(f"Total winnings: {total_winnings:.4f}, max loss: {max_price:.4f}, arb: {arb_pct:.4f}%")
                    arbitrage_per_price_type[price_type] = arb_pct
                else:
                    logging.warning(f"Data incomplete for trade: {trade_name}, setting arbitrage to NaN for {price_type}.")
                    arbitrage_per_price_type[price_type] = np.nan

            elif method == 'balanced':
                # For 'balanced' method
                total_cost = 0
                for slug, outcome in side_a_positions + side_b_positions:
                    logging.info(f"Processing balanced strategy: {trade_name} using {price_type} prices")

                    token_id = slug_to_token_id.get(slug, {}).get(outcome)
                    if not token_id:
                        logging.warning(f"Token ID not found for {slug} ({outcome}), skipping this pair.")
                        data_complete = False
                        break

                    if price_type == 'actual':
                        price, size = get_actual_price(slug, outcome, user_id)
                        if price is None:
                            logging.warning(f"Actual price not found for {slug} ({outcome}), skipping this pair.")
                            data_complete = False
                            break
                    elif price_type == 'live':
                        price = get_live_price(token_id, side='sell' if outcome.lower() == 'no' else 'buy')
                    else:
                        file_name = f"{slug}_{outcome}.csv"
                        file_path = os.path.join(data_dir, file_name)
                        if not os.path.exists(file_path):
                            logging.info(f"File not found: {file_path}, skipping this pair.")
                            data_complete = False
                            break
                        df = pd.read_csv(file_path)
                        price, _ = get_price_and_size(df, price_type)
                        if price is None:
                            logging.warning(f"Price not found in file for {slug} ({outcome}), skipping this pair.")
                            data_complete = False
                            break

                    try:
                        price = float(price)
                    except (TypeError, ValueError) as e:
                        logging.error(f"Failed to convert price to float for {slug} ({outcome}): {e}")
                        data_complete = False
                        break

                    total_cost += price
                    logging.debug(f"Adding cost of {price:.4f} for {slug} ({outcome}) to total.")

                if data_complete and total_cost > 0:
                    profit = 1 - total_cost
                    arb_pct = profit * 100
                    logging.info(f"Total cost: {total_cost:.4f}, profit: {profit:.4f}, arb: {arb_pct:.4f}%")
                    arbitrage_per_price_type[price_type] = arb_pct
                else:
                    logging.warning(f"Data incomplete for trade: {trade_name}, setting arbitrage to NaN for {price_type}.")
                    arbitrage_per_price_type[price_type] = np.nan

        if arbitrage_per_price_type:
            arbitrage_info[trade_name] = arbitrage_per_price_type
            logging.info(f"\nArbitrage opportunity for {trade_name}: {arbitrage_info[trade_name]}")
        else:
            logging.info(f"No arbitrage opportunities found for {trade_name}.")

    return arbitrage_info
def get_spread_from_api(slug, outcome, slug_to_token_id):
    token_id = slug_to_token_id.get(slug, {}).get(outcome)
    if not token_id:
        logging.warning(f"Token ID not found for {slug} ({outcome})")
        return None

    try:
        spread_info = client.get_spread(token_id=token_id)
        if spread_info and 'spread' in spread_info:
            try:
                spread_value = float(spread_info['spread'])
                return spread_value
            except ValueError:
                logging.error(f"Spread value is not a float: {spread_info['spread']}")
                return None
    except Exception as e:
        logging.error(f"Failed to fetch spread for token {token_id}: {str(e)}")
    return None


def process_all_trades(trades, output_dir='./strategies', include_bid=True):
    """
    Process all trades, saving the results to CSV and HTML.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Default user ID
    user_id = 'JeremyRWhittaker'

    # Run get_user_trade_prices.py as a subprocess with --run-once
    try:
        logging.info(f"Updating user trades for user: {user_id}")
        subprocess.run(
            ['python', 'get_user_trade_prices.py', user_id, './data/strategies.py'],
            check=True
        )
        logging.info(f"Successfully updated user trades for user: {user_id}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error updating user trades: {e}")
        return  # Exit the function if updating trades is critical
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    slug_to_token_id = load_market_lookup()
    arbitrage_info = {}
    datasets = {}
    spread_info = {}
    trade_descriptions = {}

    # Determine the price types based on the include_bid flag
    price_types = ['ask', 'mid', 'live', 'actual']
    if include_bid:
        price_types.append('bid')

    user_id = 'JeremyRWhittaker'  # Default user ID

    for trade in trades:
        trade_name = trade['trade_name']
        trade_descriptions[trade_name] = trade.get('description', '')  # Store the description
        trade_datasets = {}
        trade_spreads = {}

        if trade['method'] == 'all_no':
            for price_type in price_types:
                save_trade_details_with_prices(trade, ['positions'], price_type, output_dir, slug_to_token_id, user_id)
                # Load and store dataset
                dataset_path = os.path.join(output_dir, f"{trade_name}_{price_type}.csv")
                if os.path.exists(dataset_path):
                    trade_datasets[price_type] = pd.read_csv(dataset_path)
        elif trade['method'] == 'balanced':
            for price_type in price_types:
                save_trade_details_with_prices(trade, ['side_a_trades', 'side_b_trades'], price_type, output_dir,
                                               slug_to_token_id, user_id)
                # Load and store dataset
                dataset_path = os.path.join(output_dir, f"{trade_name}_{price_type}.csv")
                if os.path.exists(dataset_path):
                    trade_datasets[price_type] = pd.read_csv(dataset_path)

        # Get the spread for the trade using the token ID
        for side in ['positions', 'side_a_trades', 'side_b_trades']:
            trade_sides = trade.get(side, [])
            for slug, outcome in trade_sides:
                try:
                    logging.info(f"Processing trade side {side} for slug: {slug} and outcome: {outcome}")
                    token_id = slug_to_token_id.get(slug, {}).get(outcome)
                    if token_id:
                        logging.info(f"Token ID found for {slug} ({outcome}): {token_id}")
                        spread = get_spread_from_api(slug, outcome, slug_to_token_id)
                        if spread is not None:
                            trade_spreads[slug] = spread
                            logging.info(f"Spread for {slug} ({outcome}): {spread}")
                        else:
                            logging.warning(f"No spread found for {slug} ({outcome})")
                    else:
                        logging.warning(f"Token ID not found for {slug} ({outcome})")
                except Exception as e:
                    logging.error(f"Error processing trade side {side} for {slug} ({outcome}): {e}", exc_info=True)

        # Store the datasets and spreads
        try:
            logging.info(f"Storing datasets and spread information for trade: {trade_name}")
            datasets[trade_name] = trade_datasets
            spread_info[trade_name] = trade_spreads
        except Exception as e:
            logging.error(f"Error storing datasets and spreads for trade: {trade_name}: {e}", exc_info=True)

    # Calculate arbitrage opportunities for all trades at once
    try:
        logging.info("Calculating arbitrage for all trades")
        arbitrage_info = calculate_arbitrage_for_scenarios(trades, price_types=price_types, user_id=user_id)
    except Exception as e:
        logging.error(f"Error calculating arbitrage: {e}", exc_info=True)

    # Save summary and datasets to HTML, including trade descriptions
    try:
        logging.info("Saving summary and datasets to HTML")
        save_summary_to_html_with_datasets(arbitrage_info, datasets, spread_info, output_dir, trade_descriptions)
    except Exception as e:
        logging.error(f"Error saving summary and datasets to HTML: {e}", exc_info=True)

    # Optionally save to CSV as well
    try:
        logging.info("Saving summary to CSV")
        save_summary_to_csv(arbitrage_info, output_dir, datasets)
    except Exception as e:
        logging.error(f"Error saving summary to CSV: {e}", exc_info=True)

def save_summary_to_csv(arbitrage_info, output_dir, datasets):
    """
    Save a summary of arbitrage opportunities to a CSV file.
    """
    summary_data = []

    # Populate summary data
    for trade_name, arb_data in arbitrage_info.items():
        for price_type, arb_value in arb_data.items():
            summary_data.append({
                'Trade Name': trade_name,
                'Price Type': price_type,
                'Arbitrage %': arb_value
            })

    if summary_data:
        df_summary = pd.DataFrame(summary_data)

        # Sort by Arbitrage %
        df_summary.sort_values(by=['Arbitrage %'], ascending=False, inplace=True)
    else:
        # If no data is found, create an empty DataFrame with appropriate columns
        df_summary = pd.DataFrame(columns=['Trade Name', 'Price Type', 'Arbitrage %'])

    summary_path = os.path.join(output_dir, "summary.csv")
    df_summary.to_csv(summary_path, index=False)
    logging.info("Summary results exported to %s", summary_path)
def save_summary_to_html_with_datasets(arbitrage_info, datasets, spread_info, output_dir, trade_descriptions):
    """
    Save a summary of arbitrage opportunities to an HTML file using Jinja2 templates,
    with links to the corresponding detailed datasets.
    """
    import numpy as np  # Ensure numpy is imported

    # Get the current time in Arizona time zone
    arizona_tz = pytz.timezone('America/Phoenix')
    run_time = datetime.now(arizona_tz).strftime('%Y-%m-%d %H:%M:%S %Z')

    # Prepare data to be passed to the template
    trades_summary = []
    trades_list = []

    for trade_name, arb_data in arbitrage_info.items():
        # Get the description
        description = trade_descriptions.get(trade_name, '')
        # Remove the spreads from the trade name
        trade_name_with_spread = trade_name  # Not including spreads

        # Prepare list of price types and arbitrage values for this trade
        price_types_data = []
        ask_arbitrage_num = None  # Initialize to store ask arbitrage value

        for price_type, arb_value in arb_data.items():
            link_id = f"{trade_name.replace(' ', '_').replace('/', '-')}_{price_type}"
            try:
                arb_num = float(arb_value)
                if np.isnan(arb_num):
                    arb_num = float('-inf')
                    arb_str = 'NaN'
                else:
                    arb_str = f"{arb_num:.2f}"
            except (TypeError, ValueError):
                arb_num = float('-inf')
                arb_str = 'NaN'

            price_types_data.append({
                'price_type': price_type,
                'arbitrage': arb_str,
                'arbitrage_num': arb_num,
                'link_id': link_id
            })

            # Store ask arbitrage value
            if price_type == 'ask':
                ask_arbitrage_num = arb_num

            # Get the dataset HTML
            dataset = datasets.get(trade_name, {}).get(price_type)
            if dataset is not None:
                dataset_html = dataset.to_html(index=False)
            else:
                dataset_html = "<p>No data available for this trade and price type.</p>"

            trades_list.append({
                'trade_name': trade_name_with_spread,
                'price_type': price_type,
                'link_id': link_id,
                'dataset_html': dataset_html
            })

        # Sort price_types_data, ensure 'ask' is first
        price_types_data.sort(key=lambda x: 0 if x['price_type'] == 'ask' else 1)

        # Add ask_arbitrage_num to trade data
        trades_summary.append({
            'trade_name': trade_name_with_spread,
            'description': description,
            'price_types': price_types_data,
            'ask_arbitrage_num': ask_arbitrage_num
        })

    # Now sort trades_summary by 'ask_arbitrage_num' descending, handling NaN values
    def sort_key(x):
        ask_arb = x.get('ask_arbitrage_num')
        if ask_arb is None or np.isnan(ask_arb):
            return float('-inf')  # Treat NaN and None as the lowest value
        else:
            return ask_arb

    trades_summary.sort(key=sort_key, reverse=True)

    # Prepare context for the template
    context = {
        'run_time': run_time,
        'trades_summary': trades_summary,
        'trades': trades_list
    }

    # Load the Jinja2 template with CSS styling
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
        <meta http-equiv="Pragma" content="no-cache">
        <meta http-equiv="Expires" content="0">
        <title>Arbitrage Summary</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 20px;
                background-color: #f9f9f9;
            }
            h1, h2, h3 {
                color: #333;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 20px;
                background-color: #fff;
            }
            th, td {
                padding: 12px;
                border: 1px solid #ddd;
                text-align: left;
            }
            th {
                background-color: #f4f4f4;
            }
            .arb-positive {
                background-color: #d4edda !important; /* Light green */
            }
            a {
                color: #3498db;
                text-decoration: none;
            }
            a:hover {
                text-decoration: underline;
            }
            .timestamp {
                font-size: 0.9em;
                color: #777;
            }
            .trade-section {
                margin-bottom: 30px;
                padding-bottom: 10px;
                border-bottom: 1px solid #ccc;
            }
            .trade-description {
                font-style: italic;
                margin-bottom: 10px;
            }
        </style>
    </head>
    <body>
        <h1>Arbitrage Summary</h1>
        <p class="timestamp">Updated at: {{ run_time }}</p>

        {% for trade in trades_summary %}
        <div class="trade-section">
            <h2>{{ trade.trade_name }}</h2>
            {% if trade.description %}
            <p class="trade-description">{{ trade.description }}</p>
            {% endif %}
            <table>
                <thead>
                    <tr>
                        <th>Price Type</th>
                        <th>Arbitrage %</th>
                    </tr>
                </thead>
                <tbody>
                    {% for item in trade.price_types %}
                    <tr class="{% if item.price_type == 'ask' and item.arbitrage_num > 0 %}arb-positive{% endif %}">
                        <td><a href="#{{ item.link_id }}">{{ item.price_type }}</a></td>
                        <td>{{ item.arbitrage }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endfor %}

        {% for trade in trades %}
        <h2 id="{{ trade.link_id }}">{{ trade.trade_name }} ({{ trade.price_type }})</h2>
        {{ trade.dataset_html | safe }}
        {% endfor %}

        <script>
            // Auto-reload the page every 30 seconds to reflect new updates
            setInterval(function() {
                window.location.reload(true);  // Force reload without using the cache
            }, 30000);  // Reload every 30 seconds
        </script>
    </body>
    </html>
    """

    # Create a Jinja2 environment and render the template
    template = jinja2.Template(html_template)
    rendered_html = template.render(context)

    # Write to a temporary file and then replace the original file atomically
    summary_html_path = os.path.join(output_dir, "summary.html")

    import tempfile
    try:
        # Write to a temporary file
        with tempfile.NamedTemporaryFile('w', delete=False, dir=output_dir, prefix='summary_', suffix='.html') as tmp_file:
            tmp_file.write(rendered_html)
            temp_file_path = tmp_file.name

        # Atomically replace the old file with the new file
        os.replace(temp_file_path, summary_html_path)
        logging.info("Summary with datasets exported to %s", summary_html_path)
    except Exception as e:
        logging.error(f"Error saving summary HTML file: {e}", exc_info=True)



def run_continuously(trades, output_dir='./strategies', include_bid=True, interval=300):
    """
    Run the process_all_trades function every 'interval' seconds.
    """
    while True:
        try:
            # First, update the order books
            logging.info("Updating order books before processing trades.")
            update_books_for_trades()

            # Run the main processing function
            process_all_trades(trades, output_dir=output_dir, include_bid=include_bid)

            # Log the completion of one iteration
            logging.info("Completed one iteration of process_all_trades.")

            # Sleep for the specified interval (300 seconds = 5 minutes)
            time.sleep(interval)

        except Exception as e:
            logging.error(f"An error occurred: {e}")
            # Sleep for a bit before trying again in case of error
            time.sleep(interval)

# Example usage:
if __name__ == "__main__":
    run_continuously(trades, include_bid=True)
