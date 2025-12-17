from app import app, mail, Message, get_db_connection
from datetime import datetime

def check_and_send_reminders():
    with app.app_context():
        print("Checking for due tasks...")
        conn = get_db_connection()
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Get tasks due today that are NOT done
        # Join with users to get the email address
        query = '''
            SELECT tasks.*, users.email, users.username 
            FROM tasks 
            JOIN users ON tasks.user_id = users.id 
            WHERE tasks.due_date = ? AND tasks.done = 0
        '''
        tasks = conn.execute(query, (today,)).fetchall()
        
        if not tasks:
            print("No tasks due today!")
            return

        for task in tasks:
            email = task['email']
            if not email:
                print(f"Skipping task '{task['content']}' - User {task['username']} has no email.")
                continue
                
            print(f"Sending reminder to {email} for '{task['content']}'...")
            
            try:
                msg = Message(f"Reminder: {task['content']} is due today!",
                              recipients=[email])
                msg.body = f"Hello {task['username']},\n\nJust a friendly reminder that your task '{task['content']}' is due today ({today}).\n\nGet it done!\n\n- Modern To-Do App"
                mail.send(msg)
                print("Email sent successfully!")
            except Exception as e:
                print(f"Failed to send email: {e}")

        conn.close()

if __name__ == "__main__":
    check_and_send_reminders()
