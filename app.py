import os
import random
import threading
import time
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# --- INDIAN STOCKS CONFIGURATION (From your snippet) ---
STOCKS_LIST = [
    {"symbol": "RELIANCE", "name": "Reliance Ind.", "price": 2450.00, "sector": "Energy"},
    {"symbol": "TCS", "name": "Tata Consultancy", "price": 3680.00, "sector": "IT"},
    {"symbol": "INFY", "name": "Infosys Ltd", "price": 1520.00, "sector": "IT"},
    {"symbol": "HDFCBANK", "name": "HDFC Bank", "price": 1680.00, "sector": "Banking"},
    {"symbol": "ICICIBANK", "name": "ICICI Bank", "price": 980.00, "sector": "Banking"},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel", "price": 1240.00, "sector": "Telecom"},
    {"symbol": "ITC", "name": "ITC Limited", "price": 445.00, "sector": "FMCG"},
    {"symbol": "WIPRO", "name": "Wipro Ltd", "price": 485.00, "sector": "IT"},
    {"symbol": "TATAMOTORS", "name": "Tata Motors", "price": 765.00, "sector": "Auto"},
    {"symbol": "ADANIENT", "name": "Adani Ent", "price": 2890.00, "sector": "Infra"}
]

# --- GLOBAL STATE ---
game_state = {
    "status": "lobby", 
    "game_time": 0,
    "day": 1,
    "day_progress": 0,
    "stocks": {},
    "users": {}, 
    "pending_orders": [],
    "active_pattern": None,
    "pattern_tick": 0
}

# Initialize Stocks with History Arrays
for s in STOCKS_LIST:
    game_state['stocks'][s['symbol']] = {
        "price": s['price'],
        "open_price": s['price'],
        "history": [], # Stores candles
        "price_stream": [], # Stores raw prices for RSI/MACD calc
        "current_candle": {"open": s['price'], "high": s['price'], "low": s['price'], "close": s['price']}
    }

# --- TECHNICAL INDICATOR MATH (Translated from your Node.js code) ---
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50 # Default neutral
    gains, losses = 0, 0
    for i in range(1, period + 1):
        change = prices[-i] - prices[-(i+1)]
        if change > 0: gains += change
        else: losses -= change
    
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_sma(prices, period=20):
    if len(prices) < period: return prices[-1]
    return sum(prices[-period:]) / period

def market_engine():
    """ The Brain: Runs 1 tick per second """
    while True:
        socketio.sleep(1)
        
        if game_state['status'] != 'active':
            continue
            
        game_state['game_time'] += 1
        game_state['day_progress'] += 1
        
        # Day Change Logic (5 mins = 300s)
        if game_state['day_progress'] >= 300:
            game_state['day'] += 1
            game_state['day_progress'] = 0
            socketio.emit('day_change', {'day': game_state['day']})
            if game_state['day'] > 12:
                game_state['status'] = 'ended'
                calculate_winner()
                continue

        market_update = {}
        
        for symbol, data in game_state['stocks'].items():
            current_price = data['price']
            change = 0
            
            # --- SCENARIO INJECTION (The Teacher's Control) ---
            if game_state['active_pattern']:
                tick = game_state['pattern_tick']
                target = game_state['active_pattern']
                
                # Logic to force candle shapes
                if target == 'hammer': 
                    if tick < 7: change = -random.uniform(2, 5) # Crash
                    else: change = random.uniform(3, 6) # Recover
                elif target == 'bullish_engulfing':
                     change = random.uniform(2, 4) # Strong Up
                elif target == 'bearish_engulfing':
                     change = -random.uniform(2, 4) # Strong Down
                elif target == 'doji':
                    if tick < 5: change = random.uniform(-3, 3)
                    else: change = (data['open_price'] - current_price) / (10 - tick) # Return to open
            else:
                # Normal Random Walk
                change = random.uniform(-2, 2)

            new_price = round(current_price + change, 2)
            if new_price < 1: new_price = 1
            
            data['price'] = new_price
            data['price_stream'].append(new_price)
            if len(data['price_stream']) > 200: data['price_stream'].pop(0) # Keep memory low
            
            # Candle Update
            cc = data['current_candle']
            cc['close'] = new_price
            if new_price > cc['high']: cc['high'] = new_price
            if new_price < cc['low']: cc['low'] = new_price
            
            # --- 10 SECOND CANDLE CLOSE ---
            if game_state['game_time'] % 10 == 0:
                candle_data = {
                    "time": game_state['game_time'],
                    "open": cc['open'], "high": cc['high'], "low": cc['low'], "close": cc['close']
                }
                data['history'].append(candle_data)
                
                # Reset Candle
                data['open_price'] = new_price
                data['current_candle'] = {"open": new_price, "high": new_price, "low": new_price, "close": new_price}
                
                # Calculate Indicators
                rsi = calculate_rsi(data['price_stream'])
                sma = calculate_sma(data['price_stream'])
                
                socketio.emit('candle_close', {
                    'symbol': symbol, 
                    'candle': candle_data,
                    'indicators': {'rsi': round(rsi, 2), 'sma': round(sma, 2)}
                })

            market_update[symbol] = new_price
            check_stop_losses(symbol, new_price)

        # Increment Pattern Timer
        if game_state['active_pattern']:
            game_state['pattern_tick'] += 1
            if game_state['pattern_tick'] >= 10: 
                game_state['active_pattern'] = None
                game_state['pattern_tick'] = 0

        socketio.emit('price_tick', market_update)
        
        if game_state['game_time'] % 2 == 0:
            push_leaderboard()

def check_stop_losses(symbol, price):
    triggered = []
    for order in game_state['pending_orders']:
        if order['symbol'] == symbol:
            if order['type'] == 'stop_loss' and price <= float(order['trigger']):
                execute_trade(order['username'], symbol, 'sell', order['qty'], price)
                triggered.append(order)
                socketio.emit('notification', {'username': order['username'], 'msg': f"Stop Loss Triggered: {symbol}"})
    for t in triggered:
        if t in game_state['pending_orders']: game_state['pending_orders'].remove(t)

def execute_trade(username, symbol, side, qty, price):
    user = game_state['users'].get(username)
    if not user: return False
    cost = price * int(qty)
    
    if side == 'buy':
        if user['cash'] >= cost:
            user['cash'] -= cost
            user['holdings'][symbol] += int(qty)
            return True
    elif side == 'sell':
        user['cash'] += cost
        user['holdings'][symbol] -= int(qty)
        return True
    return False

def calculate_winner():
    lb = get_leaderboard_data()
    if lb: socketio.emit('game_over', {'winner': lb[0]['name'], 'return': lb[0]['value']})

def get_leaderboard_data():
    lb = []
    for u, data in game_state['users'].items():
        val = data['cash']
        for sym, qty in data['holdings'].items():
            val += qty * game_state['stocks'][sym]['price']
        lb.append({"name": u, "value": round(val, 2)})
    lb.sort(key=lambda x: x['value'], reverse=True)
    return lb

def push_leaderboard():
    socketio.emit('leaderboard_update', get_leaderboard_data())

# --- ROUTES ---
@app.route('/')
def index(): return render_template('login.html')

@app.route('/game')
def game(): return render_template('game.html', stocks=STOCKS_LIST)

@app.route('/admin')
def admin(): return render_template('admin.html')

# --- SOCKET EVENTS ---
@socketio.on('join_game')
def handle_join(data):
    username = data['username']
    if username not in game_state['users']:
        game_state['users'][username] = {"cash": 1000000, "holdings": {s['symbol']: 0 for s in STOCKS_LIST}}
    
    emit('game_status', {'status': game_state['status'], 'day': game_state['day']})
    emit('init_data', {'portfolio': game_state['users'][username], 'stocks': game_state['stocks']})

@socketio.on('place_order')
def handle_order(data):
    if game_state['status'] != 'active':
        emit('order_result', {'msg': "Market Closed!"}); return

    if data['order_type'] == 'market':
        price = game_state['stocks'][data['symbol']]['price']
        success = execute_trade(data['username'], data['symbol'], data['side'], int(data['qty']), price)
        msg = "Executed" if success else "Failed"
        emit('order_result', {'msg': msg})
        emit('portfolio_update', game_state['users'][data['username']])
    elif data['order_type'] == 'stop_loss':
        game_state['pending_orders'].append({
            "username": data['username'], "symbol": data['symbol'], 
            "qty": int(data['qty']), "type": "stop_loss", "trigger": data['trigger']
        })
        emit('order_result', {'msg': "SL Set"})

@socketio.on('admin_action')
def handle_admin(data):
    action = data['action']
    if action == 'start': game_state['status'] = 'active'
    elif action == 'pause': game_state['status'] = 'paused'
    elif action == 'resume': game_state['status'] = 'active'
    elif action in ['hammer', 'doji', 'bullish_engulfing', 'bearish_engulfing']:
        game_state['active_pattern'] = action
        game_state['pattern_tick'] = 0
    socketio.emit('game_status', {'status': game_state['status'], 'day': game_state['day']})

if __name__ == '__main__':
    t = threading.Thread(target=market_engine)
    t.daemon = True
    t.start()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
