from flask import Flask, render_template, request, redirect, url_for, make_response, flash
import os
import csv
import io
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
# Secret key
app.secret_key = os.environ.get('SECRET_KEY', 'super_secret_key_for_dev_only')

# Database Configuration
# Use Render's DATABASE_URL if available, otherwise local SQLite
database_url = os.environ.get('DATABASE_URL', 'sqlite:///todo.db')
# Fix for Render's postgres:// usage (SQLAlchemy needs postgresql://)
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Email Configuration
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'your-email@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'your-app-password')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'your-email@gmail.com')

try:
    from flask_mail import Mail, Message
    mail = Mail(app)
except ImportError:
    mail = None
    Message = None

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- Models ---
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    email = db.Column(db.String(150))
    
    tasks = db.relationship('Task', backref='owner', lazy=True)
    shared_tasks = db.relationship('TaskShare', backref='user', lazy=True)

class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(500), nullable=False)
    done = db.Column(db.Boolean, default=False)
    priority = db.Column(db.String(50), default='Medium')
    due_date = db.Column(db.String(50))
    category = db.Column(db.String(50), default='Personal')
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    shares = db.relationship('TaskShare', backref='task', lazy=True, cascade="all, delete-orphan")

class TaskShare(db.Model):
    __tablename__ = 'task_shares'
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    permission = db.Column(db.String(50), default='view')

# --- Helpers ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Routes ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists!')
            return redirect(url_for('register'))
        
        hashed_pw = generate_password_hash(password)
        new_user = User(username=username, password_hash=hashed_pw)
        
        try:
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful! Please login.')
            return redirect(url_for('login'))
        except Exception as e:
            flash(f'Error: {e}')
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
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
    if request.method == 'POST':
        email = request.form['email']
        current_user.email = email
        try:
            db.session.commit()
            flash('Profile updated successfully!')
        except Exception as e:
            flash(f'Error updating profile: {e}')
            
    return render_template('profile.html')

@app.route('/')
@login_required
def index():
    category_filter = request.args.get('category', 'All')
    sort_by = request.args.get('sort', 'newest')
    
    # Base query: My tasks OR Tasks shared with me
    # SQLAlchemy construct for OR condition
    shared_task_ids = [share.task_id for share in TaskShare.query.filter_by(user_id=current_user.id).all()]
    
    query = Task.query.filter(
        (Task.user_id == current_user.id) | (Task.id.in_(shared_task_ids))
    )
    
    if category_filter != 'All':
        query = query.filter_by(category=category_filter)
        
    if sort_by == 'due_date':
        query = query.order_by(Task.due_date.asc())
    elif sort_by == 'oldest':
        query = query.order_by(Task.id.asc())
    else: # newest
        query = query.order_by(Task.id.desc())
        
    tasks = query.all()
    
    # Custom python sort for Priority if needed (SQLAlchemy custom sort is verbose)
    if sort_by == 'priority':
        priority_order = {'High': 1, 'Medium': 2, 'Low': 3}
        tasks.sort(key=lambda x: priority_order.get(x.priority, 4))

    return render_template('index.html', tasks=tasks, 
                         current_category=category_filter, 
                         current_sort=sort_by)

@app.route('/share/<int:id>', methods=['GET', 'POST'])
@login_required
def share_task(id):
    if request.method == 'POST':
        username = request.form['username']
        user_to_share = User.query.filter_by(username=username).first()
        
        if user_to_share:
            existing = TaskShare.query.filter_by(task_id=id, user_id=user_to_share.id).first()
            if not existing:
                new_share = TaskShare(task_id=id, user_id=user_to_share.id)
                db.session.add(new_share)
                db.session.commit()
                flash(f'Task shared with {username}!')
            else:
                flash(f'Already shared with {username}.')
        else:
            flash('User not found.')
        return redirect(url_for('index'))
        
    task = Task.query.filter_by(id=id, user_id=current_user.id).first()
    
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
        new_task = Task(
            content=content,
            priority=priority,
            category=category,
            due_date=due_date,
            user_id=current_user.id
        )
        db.session.add(new_task)
        db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete_task(id):
    task = Task.query.filter_by(id=id, user_id=current_user.id).first()
    if task:
        db.session.delete(task)
        db.session.commit()
    return redirect(url_for('index'))

@app.route('/toggle/<int:id>', methods=['POST'])
@login_required
def toggle_task(id):
    task = Task.query.filter_by(id=id, user_id=current_user.id).first()
    if task:
        task.done = not task.done
        db.session.commit()
    return redirect(url_for('index'))

@app.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_task(id):
    task = Task.query.filter_by(id=id, user_id=current_user.id).first()
    
    if not task:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        task.content = request.form.get('task')
        task.priority = request.form.get('priority')
        task.category = request.form.get('category')
        task.due_date = request.form.get('due_date')
        db.session.commit()
        return redirect(url_for('index'))
    
    return render_template('edit.html', task=task)

@app.route('/export')
@login_required
def export_tasks():
    tasks = Task.query.filter_by(user_id=current_user.id).all()

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['ID', 'Task', 'Done', 'Priority', 'Due Date', 'Category'])
    for task in tasks:
        cw.writerow([task.id, task.content, task.done, task.priority, task.due_date, task.category])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=tasks.csv"
    output.headers["Content-type"] = "text/csv"
    return output

# Create tables if they don't exist (Runs on Render startup)
with app.app_context():
    db.create_all()

@app.route('/init-db')
def init_db():
    try:
        db.create_all()
        return 'Database tables created successfully! <a href="/login">Go to Login</a>'
    except Exception as e:
        return f'An error occurred: {e}'

if __name__ == '__main__':
    app.run(debug=True, port=9000)
