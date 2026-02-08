import os
import random
import threading
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
# 'cors_allowed_origins' is vital for cloud security
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# --- GAME CONFIGURATION ---
STOCKS_LIST = [
    {"symbol": "RELIANCE", "name": "Reliance Ind.", "price": 2400},
    {"symbol": "TCS", "name": "Tata Consultancy", "price": 3500},
    {"symbol": "HDFCBANK", "name": "HDFC Bank", "price": 1600},
    {"symbol": "INFY", "name": "Infosys Ltd", "price": 1400},
    {"symbol": "ICICI", "name": "ICICI Bank", "price": 950},
    {"symbol": "SBIN", "name": "State Bank India", "price": 600},
    {"symbol": "ADANI", "name": "Adani Ent", "price": 2000},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance", "price": 7000},
    {"symbol": "WIPRO", "name": "Wipro Ltd", "price": 400},
    {"symbol": "TITAN", "name": "Titan Company", "price": 3000}
]

# --- GLOBAL STATE (Stored in Memory) ---
game_state = {
    "is_running": False,
    "is_paused": False,
    "game_time": 0,
    "day": 1,
    "stocks": {},
    "users": {}
}

for s in STOCKS_LIST:
    game_state['stocks'][s['symbol']] = {
        "price": s['price'],
        "history": [],
        "current_candle": {"open": s['price'], "high": s['price'], "low": s['price'], "close": s['price']}
    }

def market_engine():
    """ Runs every 1 second to update prices """
    ticks = 0
    while True:
        socketio.sleep(1)
        if not game_state['is_running'] or game_state['is_paused']:
            continue
            
        game_state['game_time'] += 1
        ticks += 1
        
        # 5 mins (300 ticks) = 1 Day
        if game_state['game_time'] % 300 == 0:
            game_state['day'] += 1
            socketio.emit('day_change', {'day': game_state['day']})

        market_update = {}
        for symbol, data in game_state['stocks'].items():
            change = random.uniform(-2, 2)
            new_price = round(data['price'] + change, 2)
            data['price'] = new_price
            
            # Candle Logic
            cc = data['current_candle']
            cc['close'] = new_price
            if new_price > cc['high']: cc['high'] = new_price
            if new_price < cc['low']: cc['low'] = new_price
            
            # Close Candle every 10 seconds
            if ticks % 10 == 0:
                candle_data = {
                    "time": game_state['game_time'],
                    "open": cc['open'], "high": cc['high'], "low": cc['low'], "close": cc['close']
                }
                data['history'].append(candle_data)
                data['current_candle'] = {"open": new_price, "high": new_price, "low": new_price, "close": new_price}
                socketio.emit('candle_close', {'symbol': symbol, 'candle': candle_data})

            market_update[symbol] = new_price

        socketio.emit('price_tick', market_update)
        
        if ticks % 2 == 0:
            push_leaderboard()

def push_leaderboard():
    leaderboard = []
    for username, user in game_state['users'].items():
        total_val = user['cash']
        for symbol, qty in user['holdings'].items():
            total_val += qty * game_state['stocks'][symbol]['price']
        leaderboard.append({"name": username, "value": round(total_val, 2)})
    
    leaderboard.sort(key=lambda x: x['value'], reverse=True)
    socketio.emit('leaderboard_update', leaderboard)

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
        game_state['users'][username] = {
            "cash": 100000,
            "holdings": {s['symbol']: 0 for s in STOCKS_LIST},
            "trades": []
        }
    emit('init_game', {'portfolio': game_state['users'][username], 'stocks': game_state['stocks']})

@socketio.on('place_order')
def handle_order(data):
    user = game_state['users'].get(data['username'])
    if not user: return
    symbol, qty, otype = data['symbol'], int(data['qty']), data['type']
    price = game_state['stocks'][symbol]['price']
    cost = price * qty
    
    if otype == 'buy':
        if user['cash'] >= cost:
            user['cash'] -= cost
            user['holdings'][symbol] += qty
            emit('order_result', {'msg': f"Bought {qty} {symbol}"})
        else: emit('order_result', {'msg': "No Cash"})
    elif otype == 'sell':
        user['cash'] += cost
        user['holdings'][symbol] -= qty
        emit('order_result', {'msg': f"Sold {qty} {symbol}"})
    emit('portfolio_update', user)

@socketio.on('admin_control')
def handle_admin(data):
    if data['action'] == 'start': game_state['is_running'] = True
    elif data['action'] == 'pause': game_state['is_paused'] = True
    elif data['action'] == 'resume': game_state['is_paused'] = False

if __name__ == '__main__':
    # Start engine in background
    t = threading.Thread(target=market_engine)
    t.daemon = True
    t.start()
    
    # Get PORT from Environment (Required for Cloud)
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
