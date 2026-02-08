import os
import random
import threading
import time
import math
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# --- 1. GAME CONFIGURATION ---
STOCKS_LIST = [
    {"symbol": "RELIANCE", "name": "Reliance Ind.", "price": 2450.00},
    {"symbol": "TCS", "name": "Tata Consultancy", "price": 3680.00},
    {"symbol": "INFY", "name": "Infosys Ltd", "price": 1520.00},
    {"symbol": "HDFCBANK", "name": "HDFC Bank", "price": 1680.00},
    {"symbol": "ICICIBANK", "name": "ICICI Bank", "price": 980.00},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel", "price": 1240.00},
    {"symbol": "ITC", "name": "ITC Limited", "price": 445.00},
    {"symbol": "WIPRO", "name": "Wipro Ltd", "price": 485.00},
    {"symbol": "TATAMOTORS", "name": "Tata Motors", "price": 765.00},
    {"symbol": "ADANIENT", "name": "Adani Ent", "price": 2890.00}
]

# --- 2. GLOBAL STATE ---
game_state = {
    "status": "lobby", # lobby, active, paused, ended
    "game_time": 0,    # Total seconds
    "day": 1,          # Current Day (1-12)
    "day_progress": 0, # Seconds in current day (max 300)
    "stocks": {},
    "users": {},       # {username: {cash, holdings, trades}}
    "pending_orders": []
}

# Initialize Stocks
for s in STOCKS_LIST:
    game_state['stocks'][s['symbol']] = {
        "price": s['price'],
        "history": [], # Store candle objects
        "prices_raw": [s['price']] * 50, # For Indicator Calc
        "current_candle": {"open": s['price'], "high": s['price'], "low": s['price'], "close": s['price'], "volume": 0},
        
        # PATTERN ENGINE STATE
        "active_pattern": [], # List of price targets [2455, 2460, 2458...]
        "pattern_name": None
    }

# --- 3. PATTERN GENERATOR (The "Market Maker") ---
def generate_pattern(current_price, pattern_type):
    """ Generates a sequence of price targets for the next 10-30 seconds """
    targets = []
    
    if pattern_type == 'bullish_engulfing': # 2 Candles (20s)
        # Candle 1: Red (Drop)
        targets.extend([current_price * (1 - 0.002 * i) for i in range(1, 11)])
        mid_price = targets[-1]
        # Candle 2: Big Green (Recover & Overtake)
        targets.extend([mid_price * (1 + 0.005 * i) for i in range(1, 11)])
        
    elif pattern_type == 'bearish_engulfing': # 2 Candles (20s)
        # Candle 1: Green
        targets.extend([current_price * (1 + 0.002 * i) for i in range(1, 11)])
        mid_price = targets[-1]
        # Candle 2: Big Red
        targets.extend([mid_price * (1 - 0.005 * i) for i in range(1, 11)])

    elif pattern_type == 'hammer': # 1 Candle (10s)
        # Drop fast then recover
        base = current_price
        for i in range(7): targets.append(base * (1 - 0.003 * (i+1))) # Wick down
        for i in range(3): targets.append(targets[-1] * (1 + 0.004 * (i+1))) # Body up

    elif pattern_type == 'morning_star': # 3 Candles (30s)
        # 1. Big Red
        targets.extend([current_price * (1 - 0.003 * i) for i in range(1, 11)])
        p2 = targets[-1]
        # 2. Small Doji (Indecision)
        targets.extend([p2 * (1 + random.choice([-0.001, 0.001])) for i in range(10)])
        p3 = targets[-1]
        # 3. Big Green (Reversal)
        targets.extend([p3 * (1 + 0.004 * i) for i in range(1, 11)])

    return targets

# --- 4. INDICATOR MATH ---
def calculate_indicators(prices):
    # RSI (14)
    if len(prices) < 15: return {"rsi": 50, "macd": 0, "signal": 0}
    
    gains, losses = 0, 0
    for i in range(1, 15):
        change = prices[-i] - prices[-(i+1)]
        if change > 0: gains += change
        else: losses -= change
    
    avg_gain = gains / 14
    avg_loss = losses / 14
    rsi = 100 - (100 / (1 + (avg_gain / (avg_loss if avg_loss != 0 else 1))))
    
    # MACD (12, 26, 9) - Simplified EMA
    ema12 = sum(prices[-12:]) / 12
    ema26 = sum(prices[-26:]) / 26
    macd = ema12 - ema26
    
    return {"rsi": round(rsi, 2), "macd": round(macd, 2)}

# --- 5. THE MAIN ENGINE LOOP ---
def market_engine():
    while True:
        socketio.sleep(1) # 1 Second Tick
        
        if game_state['status'] != 'active': continue
            
        game_state['game_time'] += 1
        game_state['day_progress'] += 1
        
        # Day Logic (5 mins = 300s)
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
            # A. PATTERN MANAGER
            # If no pattern active, roll dice (15% chance every 10s)
            if not data['active_pattern'] and game_state['game_time'] % 10 == 0:
                if random.random() < 0.15:
                    ptypes = ['bullish_engulfing', 'bearish_engulfing', 'hammer', 'morning_star']
                    chosen = random.choice(ptypes)
                    data['active_pattern'] = generate_pattern(data['price'], chosen)
                    data['pattern_name'] = chosen
            
            # B. PRICE MOVEMENT
            if data['active_pattern']:
                # Follow the script
                target = data['active_pattern'].pop(0)
                # Add tiny random noise to look organic
                new_price = target + random.uniform(-0.5, 0.5)
            else:
                # Random Walk
                new_price = data['price'] + random.uniform(-2, 2)
            
            # Sanity Check
            if new_price < 1: new_price = 1
            data['price'] = round(new_price, 2)
            data['prices_raw'].append(new_price)
            if len(data['prices_raw']) > 50: data['prices_raw'].pop(0)

            # C. CANDLE UPDATE
            cc = data['current_candle']
            cc['close'] = new_price
            if new_price > cc['high']: cc['high'] = new_price
            if new_price < cc['low']: cc['low'] = new_price
            
            # D. CANDLE CLOSE (Every 10s)
            if game_state['game_time'] % 10 == 0:
                inds = calculate_indicators(data['prices_raw'])
                
                # 10% Chance of False Signal (Flip RSI)
                if random.random() < 0.10:
                    inds['rsi'] = 80 if inds['rsi'] < 30 else 20
                
                candle_final = {
                    "time": game_state['game_time'],
                    "open": cc['open'], "high": cc['high'], "low": cc['low'], "close": cc['close'],
                    "rsi": inds['rsi'], "macd": inds['macd']
                }
                data['history'].append(candle_final)
                
                # Reset Candle
                data['current_candle'] = {"open": new_price, "high": new_price, "low": new_price, "close": new_price}
                
                socketio.emit('candle_close', {'symbol': symbol, 'candle': candle_final})

            market_update[symbol] = new_price
            check_stop_losses(symbol, new_price)

        socketio.emit('price_tick', market_update)
        if game_state['game_time'] % 2 == 0: push_leaderboard()

# --- 6. TRADING LOGIC ---
def check_stop_losses(symbol, price):
    triggered = []
    for order in game_state['pending_orders']:
        if order['symbol'] == symbol:
            is_sell_sl = (order['type'] == 'stop_loss_sell' and price <= order['trigger'])
            is_buy_sl = (order['type'] == 'stop_loss_buy' and price >= order['trigger'])
            
            if is_sell_sl or is_buy_sl:
                side = 'sell' if is_sell_sl else 'buy'
                execute_trade(order['username'], symbol, side, order['qty'], price)
                triggered.append(order)
                socketio.emit('notification', {'username': order['username'], 'msg': f"SL Triggered: {symbol} @ {price}"})
    
    for t in triggered:
        if t in game_state['pending_orders']: game_state['pending_orders'].remove(t)

def execute_trade(username, symbol, side, qty, price):
    user = game_state['users'].get(username)
    if not user: return False
    cost = price * qty
    
    if side == 'buy':
        if user['cash'] >= cost:
            user['cash'] -= cost
            user['holdings'][symbol] += qty
            return True
    elif side == 'sell':
        user['cash'] += cost
        user['holdings'][symbol] -= qty
        return True
    return False

def push_leaderboard():
    lb = []
    for u, d in game_state['users'].items():
        val = d['cash']
        for s, q in d['holdings'].items():
            val += q * game_state['stocks'][s]['price']
        lb.append({"name": u, "value": round(val, 2)})
    lb.sort(key=lambda x: x['value'], reverse=True)
    socketio.emit('leaderboard_update', lb)

def calculate_winner():
    lb = []
    for u, d in game_state['users'].items():
        val = d['cash']
        for s, q in d['holdings'].items():
            val += q * game_state['stocks'][s]['price']
        lb.append({"name": u, "value": round(val, 2)})
    lb.sort(key=lambda x: x['value'], reverse=True)
    if lb: socketio.emit('game_over', {'winner': lb[0]['name'], 'return': lb[0]['value']})

# --- ROUTES & EVENTS ---
@app.route('/')
def index(): return render_template('login.html')
@app.route('/game')
def game(): return render_template('game.html', stocks=STOCKS_LIST)
@app.route('/admin')
def admin(): return render_template('admin.html')

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
        emit('order_result', {'msg': "Market Closed"}); return
        
    if data['type'] == 'market':
        price = game_state['stocks'][data['symbol']]['price']
        success = execute_trade(data['username'], data['symbol'], data['side'], int(data['qty']), price)
        emit('order_result', {'msg': "Filled" if success else "No Funds"})
        emit('portfolio_update', game_state['users'][data['username']])
    elif 'stop_loss' in data['type']:
        game_state['pending_orders'].append({
            "username": data['username'], "symbol": data['symbol'], 
            "qty": int(data['qty']), "type": data['type'], "trigger": float(data['trigger'])
        })
        emit('order_result', {'msg': "SL Order Set"})

@socketio.on('admin_action')
def handle_admin(data):
    action = data['action']
    if action == 'start': game_state['status'] = 'active'
    elif action == 'pause': game_state['status'] = 'paused'
    elif action == 'resume': game_state['status'] = 'active'
    socketio.emit('game_status', {'status': game_state['status'], 'day': game_state['day']})

if __name__ == '__main__':
    t = threading.Thread(target=market_engine)
    t.daemon = True
    t.start()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
