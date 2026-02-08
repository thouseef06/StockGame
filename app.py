import os
import random
import threading
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# --- 1. CONFIGURATION ---
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
    "game_time": 0,    
    "day": 1,          
    "day_progress": 0, # 0 to 300 seconds
    "stocks": {},
    "users": {},       
    "pending_orders": []
}

# Initialize Stocks
for s in STOCKS_LIST:
    game_state['stocks'][s['symbol']] = {
        "price": s['price'],
        "history": [], 
        "prices_raw": [s['price']] * 50, # Buffer for RSI calc
        "current_candle": {"open": s['price'], "high": s['price'], "low": s['price'], "close": s['price']},
        "pattern_queue": [] # Stores future price targets to form patterns
    }

# --- 3. PATTERN MATHEMATICS (The AI Director) ---
def generate_pattern_targets(current_price, pattern_type):
    targets = []
    cp = current_price
    
    # helper to generate a straight line move
    def move(start, end, steps):
        return [start + (end - start) * (i/steps) for i in range(1, steps+1)]

    if pattern_type == 'bullish_engulfing': # 2 Candles (20s)
        # Candle 1: Small Red
        targets.extend(move(cp, cp * 0.998, 10))
        # Candle 2: Big Green (Starts lower, ends higher)
        targets.extend(move(cp * 0.997, cp * 1.005, 10))
        
    elif pattern_type == 'bearish_engulfing': # 2 Candles (20s)
        # Candle 1: Small Green
        targets.extend(move(cp, cp * 1.002, 10))
        # Candle 2: Big Red
        targets.extend(move(cp * 1.003, cp * 0.995, 10))

    elif pattern_type == 'hammer': # 1 Candle (10s)
        # Deep dive then full recovery
        bottom = cp * 0.995
        targets.extend(move(cp, bottom, 6)) # Drop fast
        targets.extend(move(bottom, cp * 1.001, 4)) # Recover

    elif pattern_type == 'morning_star': # 3 Candles (30s)
        # 1. Big Red
        targets.extend(move(cp, cp * 0.990, 10))
        mid = targets[-1]
        # 2. Doji (Indecision)
        targets.extend([mid + random.uniform(-0.5, 0.5) for _ in range(10)])
        mid = targets[-1]
        # 3. Big Green
        targets.extend(move(mid, mid * 1.015, 10))
    
    elif pattern_type == 'marubozu_bull': # 1 Candle (10s) strong up
        targets.extend(move(cp, cp * 1.008, 10))

    return targets

# --- 4. ENGINE ---
def market_engine():
    while True:
        socketio.sleep(1) 
        
        # STOP if in lobby or paused
        if game_state['status'] != 'active':
            continue
            
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
            # A. AUTOMATIC SCENARIO INJECTION
            # If no pattern active, roll dice (20% chance every 10s)
            if not data['pattern_queue'] and game_state['game_time'] % 10 == 0:
                if random.random() < 0.20:
                    patterns = ['bullish_engulfing', 'bearish_engulfing', 'hammer', 'morning_star', 'marubozu_bull']
                    chosen = random.choice(patterns)
                    data['pattern_queue'] = generate_pattern_targets(data['price'], chosen)
            
            # B. EXECUTE MOVEMENT
            if data['pattern_queue']:
                # Follow the script
                target = data['pattern_queue'].pop(0)
                # Add micro-noise
                new_price = target + random.uniform(-0.2, 0.2)
            else:
                # Random Walk (Normal Market)
                new_price = data['price'] + random.uniform(-1.5, 1.5)
            
            if new_price < 1: new_price = 1
            data['price'] = round(new_price, 2)
            data['prices_raw'].append(new_price)
            if len(data['prices_raw']) > 60: data['prices_raw'].pop(0)

            # C. UPDATE CANDLE
            cc = data['current_candle']
            cc['close'] = new_price
            if new_price > cc['high']: cc['high'] = new_price
            if new_price < cc['low']: cc['low'] = new_price
            
            # D. CLOSE CANDLE (Every 10s)
            if game_state['game_time'] % 10 == 0:
                # Calculate RSI
                rsi = calculate_rsi(data['prices_raw'])
                # 10% False Signal Logic
                if random.random() < 0.10: rsi = 100 - rsi 
                
                candle_final = {
                    "time": game_state['game_time'],
                    "open": cc['open'], "high": cc['high'], "low": cc['low'], "close": cc['close'],
                    "rsi": round(rsi, 2)
                }
                data['history'].append(candle_final)
                data['current_candle'] = {"open": new_price, "high": new_price, "low": new_price, "close": new_price}
                
                socketio.emit('candle_close', {'symbol': symbol, 'candle': candle_final})

            market_update[symbol] = new_price
            check_stop_losses(symbol, new_price)

        socketio.emit('price_tick', market_update)
        if game_state['game_time'] % 2 == 0: push_leaderboard()

def calculate_rsi(prices):
    if len(prices) < 15: return 50
    gains, losses = 0, 0
    for i in range(1, 15):
        change = prices[-i] - prices[-(i+1)]
        if change > 0: gains += change
        else: losses -= change
    avg_gain = gains / 14
    avg_loss = losses / 14
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def check_stop_losses(symbol, price):
    triggered = []
    for order in game_state['pending_orders']:
        if order['symbol'] == symbol:
            is_sell = (order['type'] == 'stop_loss_sell' and price <= order['trigger'])
            is_buy = (order['type'] == 'stop_loss_buy' and price >= order['trigger'])
            
            if is_sell or is_buy:
                side = 'sell' if is_sell else 'buy'
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
        for s, q in d['holdings'].items(): val += q * game_state['stocks'][s]['price']
        lb.append({"name": u, "value": round(val, 2)})
    lb.sort(key=lambda x: x['value'], reverse=True)
    socketio.emit('leaderboard_update', lb)

def calculate_winner():
    lb = []
    for u, d in game_state['users'].items():
        val = d['cash']
        for s, q in d['holdings'].items(): val += q * game_state['stocks'][s]['price']
        lb.append({"name": u, "value": round(val, 2)})
    lb.sort(key=lambda x: x['value'], reverse=True)
    if lb: socketio.emit('game_over', {'winner': lb[0]['name'], 'return': lb[0]['value']})

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
    # IMPORTANT: Send current status so client knows if it should show lobby
    emit('game_status', {'status': game_state['status'], 'day': game_state['day']})
    emit('init_data', {'portfolio': game_state['users'][username], 'stocks': game_state['stocks']})

@socketio.on('place_order')
def handle_order(data):
    if game_state['status'] != 'active':
        emit('order_result', {'msg': "Market Closed"}); return
    
    if data['type'] == 'market':
        price = game_state['stocks'][data['symbol']]['price']
        success = execute_trade(data['username'], data['symbol'], data['side'], int(data['qty']), price)
        emit('order_result', {'msg': "Order Filled" if success else "No Funds"})
        emit('portfolio_update', game_state['users'][data['username']])
    else:
        game_state['pending_orders'].append({
            "username": data['username'], "symbol": data['symbol'], 
            "qty": int(data['qty']), "type": data['type'], "trigger": float(data['trigger'])
        })
        emit('order_result', {'msg': "SL Set"})

@socketio.on('admin_action')
def handle_admin(data):
    action = data['action']
    if action == 'start': 
        game_state['status'] = 'active'
        print("GAME STARTED BY ADMIN") # Debug log
    elif action == 'pause': 
        game_state['status'] = 'paused'
    elif action == 'resume': 
        game_state['status'] = 'active'
    
    # Broadcast status change to ALL clients instantly
    socketio.emit('game_status', {'status': game_state['status'], 'day': game_state['day']})

if __name__ == '__main__':
    t = threading.Thread(target=market_engine)
    t.daemon = True
    t.start()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
