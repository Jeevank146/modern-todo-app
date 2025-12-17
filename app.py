from flask import Flask, render_template, request, redirect, url_for, make_response, flash
import os
import sqlite3
import csv
import io
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
try:
    from flask_mail import Mail, Message
except ImportError:
    Mail = None
    Message = None

app = Flask(__name__)
# Secret key for sessions (Use Env Var in production)
app.secret_key = os.environ.get('SECRET_KEY', 'super_secret_key_for_dev_only')

# Email Configuration
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'your-email@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'your-app-password')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'your-email@gmail.com')

if Mail:
    mail = Mail(app)
else:
    mail = None

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

import sys

if getattr(sys, 'frozen', False):
    # If running as compiled exe, store DB in the same folder as the exe
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # If running as script, use current folder
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "todo.db")
TASK_FILE = os.path.join(BASE_DIR, "tasks.txt")

class User(UserMixin):
    def __init__(self, id, username, password_hash, email=None):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (int(user_id),)).fetchone()
    conn.close()
    if user:
        return User(user['id'], user['username'], user['password_hash'], user['email'])
    return None

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Create users table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            password_hash TEXT NOT NULL
        )
    ''')
    
    # Check if email column exists (migration for existing db)
    existing_columns = [row['name'] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if 'email' not in existing_columns:
        conn.execute('ALTER TABLE users ADD COLUMN email TEXT')
    
    # Create tasks table with user_id
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            done BOOLEAN NOT NULL DEFAULT 0,
            priority TEXT DEFAULT 'Medium',
            due_date TEXT,
            category TEXT DEFAULT 'Personal',
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

    conn.close()

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        try:
            # Check if user exists
            user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
            if user:
                flash('Username already exists!')
                conn.close()
                return redirect(url_for('register'))
            
            hashed_pw = generate_password_hash(password)
            conn.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', (username, hashed_pw))
            conn.commit()
            conn.close()
            flash('Registration successful! Please login.')
            return redirect(url_for('login'))
        except Exception as e:
            conn.close()
            flash('An error occurred.')
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password_hash'], password):
            user_obj = User(user['id'], user['username'], user['password_hash'])
            login_user(user_obj)
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    conn = get_db_connection()
    if request.method == 'POST':
        email = request.form['email']
        
        try:
            conn.execute('UPDATE users SET email = ? WHERE id = ?', (email, current_user.id))
            conn.commit()
            flash('Profile updated successfully!')
        except Exception as e:
            flash(f'Error updating profile: {e}')
            
    # Fetch latest user data
    user = conn.execute('SELECT * FROM users WHERE id = ?', (current_user.id,)).fetchone()
    conn.close()
    
    # Update current_user object (optional, but good for display)
    current_user.email = user['email']
    
    return render_template('profile.html')

@app.route('/')
@login_required
def index():
    category_filter = request.args.get('category', 'All')
    sort_by = request.args.get('sort', 'newest')
    
    conn = get_db_connection()
    
    query = '''
        SELECT tasks.*, users.username as owner_name 
        FROM tasks 
        JOIN users ON tasks.user_id = users.id
        WHERE tasks.user_id = ? 
        OR tasks.id IN (SELECT task_id FROM task_shares WHERE user_id = ?)
    '''
    params = [current_user.id, current_user.id]
    
    if category_filter != 'All':
        query += ' AND category = ?'
        params.append(category_filter)
        
    if sort_by == 'due_date':
        query += ' ORDER BY due_date ASC'
    elif sort_by == 'priority':
        # Custom sort order: High -> Medium -> Low
        query += ''' ORDER BY CASE priority 
                     WHEN 'High' THEN 1 
                     WHEN 'Medium' THEN 2 
                     WHEN 'Low' THEN 3 
                     ELSE 4 END'''
    elif sort_by == 'oldest':
        query += ' ORDER BY tasks.id ASC'
    else: # newest is default
        query += ' ORDER BY tasks.id DESC'
        
    tasks = conn.execute(query, params).fetchall()
    conn.close()
    
    return render_template('index.html', tasks=tasks, 
                         current_category=category_filter, 
                         current_sort=sort_by)

@app.route('/share/<int:id>', methods=['GET', 'POST'])
@login_required
def share_task(id):
    conn = get_db_connection()
    if request.method == 'POST':
        username = request.form['username']
        
        # Check if user exists
        user_to_share = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        
        if user_to_share:
            # Check if already shared
            existing = conn.execute('SELECT * FROM task_shares WHERE task_id = ? AND user_id = ?', 
                                  (id, user_to_share['id'])).fetchone()
            if not existing:
                conn.execute('INSERT INTO task_shares (task_id, user_id) VALUES (?, ?)', 
                           (id, user_to_share['id']))
                conn.commit()
                flash(f'Task shared with {username}!')
            else:
                flash(f'Already shared with {username}.')
        else:
            flash('User not found.')
            
        conn.close()
        return redirect(url_for('index'))
        
    task = conn.execute('SELECT * FROM tasks WHERE id = ? AND user_id = ?', (id, current_user.id)).fetchone()
    conn.close()
    
    if task is None:
        flash('You can only share your own tasks.')
        return redirect(url_for('index'))
        
    return render_template('share.html', task=task)

@app.route('/add', methods=['POST'])
@login_required
def add_task():
    content = request.form.get('task')
    priority = request.form.get('priority', 'Medium')
    category = request.form.get('category', 'Personal')
    due_date = request.form.get('due_date')
    
    if content:
        conn = get_db_connection()
        conn.execute('INSERT INTO tasks (user_id, content, priority, due_date, category) VALUES (?, ?, ?, ?, ?)',
                     (current_user.id, content, priority, due_date, category))
        conn.commit()
        conn.close()
    return redirect(url_for('index'))

@app.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete_task(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM tasks WHERE id = ? AND user_id = ?', (id, current_user.id))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/toggle/<int:id>', methods=['POST'])
@login_required
def toggle_task(id):
    conn = get_db_connection()
    # Toggle the 'done' status
    task = conn.execute('SELECT done FROM tasks WHERE id = ? AND user_id = ?', (id, current_user.id)).fetchone()
    if task:
        new_status = not task['done']
        conn.execute('UPDATE tasks SET done = ? WHERE id = ? AND user_id = ?', (new_status, id, current_user.id))
        conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_task(id):
    conn = get_db_connection()
    if request.method == 'POST':
        content = request.form.get('task')
        priority = request.form.get('priority')
        category = request.form.get('category')
        due_date = request.form.get('due_date')
        
        conn.execute('UPDATE tasks SET content = ?, priority = ?, due_date = ?, category = ? WHERE id = ? AND user_id = ?',
                     (content, priority, due_date, category, id, current_user.id))
        conn.commit()
        conn.close()
        return redirect(url_for('index'))
    
    task = conn.execute('SELECT * FROM tasks WHERE id = ? AND user_id = ?', (id, current_user.id)).fetchone()
    conn.close()
    
    if task is None:
        return redirect(url_for('index'))
        
    return render_template('edit.html', task=task)

@app.route('/export')
@login_required
def export_tasks():
    conn = get_db_connection()
    tasks = conn.execute('SELECT * FROM tasks WHERE user_id = ?', (current_user.id,)).fetchall()
    conn.close()

    si = io.StringIO()
    cw = csv.writer(si)
    # Write Header
    cw.writerow(['ID', 'Task', 'Done', 'Priority', 'Due Date', 'Category'])
    # Write Data
    for task in tasks:
        cw.writerow([task['id'], task['content'], task['done'], task['priority'], task['due_date'], task['category']])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=tasks.csv"
    output.headers["Content-type"] = "text/csv"
    return output

def update_schema():
    conn = get_db_connection()
    
    # 1. Create task_shares if not exists
    conn.execute('''
        CREATE TABLE IF NOT EXISTS task_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            permission TEXT DEFAULT 'view',
            FOREIGN KEY (task_id) REFERENCES tasks (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # 2. Add email column to users if not exists
    try:
        # Check if email column exists
        existing_columns = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
        if 'email' not in existing_columns:
            conn.execute('ALTER TABLE users ADD COLUMN email TEXT')
            
        # 3. Add user_id column to tasks if not exists (Migration from legacy)
        task_columns = [row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()]
        if 'user_id' not in task_columns:
            # Add user_id column with default value 1 (First user)
            conn.execute('ALTER TABLE tasks ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1')
            
    except Exception as e:
        print(f"Schema update error: {e}")

    conn.commit()
    conn.close()

# Ensure DB is ready (Runs on Import for WSGI)
if not os.path.exists(DB_FILE):
    init_db()
    
# Always check for schema updates
update_schema()

if __name__ == '__main__':
    app.run(debug=True, port=9000) 

