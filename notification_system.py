"""
Enhanced Notification System with Email Alerts
- Dashboard notifications for new/urgent jobs
- Scheduled email reminders for pending jobs
- Configurable email frequency
- Uses yagmail for simple email sending
"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta
import yagmail
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

DB_PATH = 'placement_portal.db'

class NotificationSystem:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.yag = None
        self.init_database()
    
    def init_database(self):
        """Initialize notification tables"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Notifications table
        c.execute('''CREATE TABLE IF NOT EXISTS notifications (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            job_id INTEGER,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            is_read BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (job_id) REFERENCES jobs(job_id)
        )''')
        
        # Email reminder settings
        c.execute('''CREATE TABLE IF NOT EXISTS email_reminder_settings (
            setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            is_enabled BOOLEAN DEFAULT 1,
            reminder_frequency_minutes INTEGER DEFAULT 1440,
            days_before_deadline INTEGER DEFAULT 3,
            email_address TEXT NOT NULL,
            last_reminder_sent TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )''')
        
        # Email logs
        c.execute('''CREATE TABLE IF NOT EXISTS email_reminder_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            jobs_count INTEGER,
            email_sent BOOLEAN,
            error_message TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )''')
        
        conn.commit()
        conn.close()
        print("✓ Notification database initialized")
    
    def create_notification(self, user_id, notification_type, title, message, job_id=None):
        """Create a new notification for user"""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute('''INSERT INTO notifications 
                        (user_id, job_id, type, title, message)
                        VALUES (?, ?, ?, ?, ?)''',
                     (user_id, job_id, notification_type, title, message))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error creating notification: {e}")
            return False
    
    def get_user_notifications(self, user_id, unread_only=False, limit=10):
        """Get notifications for a user"""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            query = '''SELECT n.*, j.company, j.position, j.deadline
                      FROM notifications n
                      LEFT JOIN jobs j ON n.job_id = j.job_id
                      WHERE n.user_id = ?'''
            
            if unread_only:
                query += ' AND n.is_read = 0'
            
            query += ' ORDER BY n.created_at DESC LIMIT ?'
            
            c.execute(query, (user_id, limit))
            notifications = c.fetchall()
            conn.close()
            
            return [dict(row) for row in notifications]
        except Exception as e:
            print(f"Error getting notifications: {e}")
            return []
    
    def mark_notification_read(self, notification_id, user_id):
        """Mark notification as read"""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute('''UPDATE notifications 
                        SET is_read = 1 
                        WHERE notification_id = ? AND user_id = ?''',
                     (notification_id, user_id))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error marking notification read: {e}")
            return False
    
    def mark_all_notifications_read(self, user_id):
        """Mark all notifications as read for user"""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute('''UPDATE notifications 
                        SET is_read = 1 
                        WHERE user_id = ? AND is_read = 0''',
                     (user_id,))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error marking all notifications read: {e}")
            return False
    
    def get_unread_count(self, user_id):
        """Get count of unread notifications"""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute('''SELECT COUNT(*) as count 
                        FROM notifications 
                        WHERE user_id = ? AND is_read = 0''',
                     (user_id,))
            
            count = c.fetchone()[0]
            conn.close()
            return count
        except Exception as e:
            print(f"Error getting unread count: {e}")
            return 0
    
    def create_job_notifications(self, job_id):
        """Create notifications for all active users about new job"""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # Get job details
            c.execute('SELECT * FROM jobs WHERE job_id = ?', (job_id,))
            job = c.fetchone()
            
            if not job:
                return
            
            # Get all active users
            c.execute('SELECT user_id FROM users')
            users = c.fetchall()
            
            # Calculate urgency
            if job['deadline']:
                deadline = datetime.strptime(job['deadline'], '%Y-%m-%d')
                days_left = (deadline - datetime.now()).days
                
                if days_left <= 3:
                    notification_type = 'critical'
                    title = f"🔥 CRITICAL: {job['company']} - Apply Now!"
                elif days_left <= 7:
                    notification_type = 'urgent'
                    title = f"⚡ URGENT: New Job at {job['company']}"
                else:
                    notification_type = 'new_job'
                    title = f"💼 New Opportunity: {job['company']}"
            else:
                notification_type = 'new_job'
                title = f"💼 New Opportunity: {job['company']}"
            
            message = f"{job['position'] or 'Position Available'}"
            if job['ctc']:
                message += f" | CTC: {job['ctc']}"
            if job['location']:
                message += f" | Location: {job['location']}"
            
            # Create notification for each user
            for user in users:
                self.create_notification(
                    user['user_id'],
                    notification_type,
                    title,
                    message,
                    job_id
                )
            
            conn.close()
            print(f"✓ Created notifications for {len(users)} users about job {job_id}")
            
        except Exception as e:
            print(f"Error creating job notifications: {e}")
    
    def get_pending_jobs_for_user(self, user_id, days_before_deadline=3):
        """Get pending jobs that user hasn't applied to with upcoming deadlines"""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            deadline_date = (datetime.now() + timedelta(days=days_before_deadline)).strftime('%Y-%m-%d')
            
            c.execute('''
                SELECT j.*,
                       CAST((julianday(j.deadline) - julianday('now')) as INTEGER) as days_remaining
                FROM jobs j
                WHERE j.job_id NOT IN (
                    SELECT job_id FROM applications WHERE user_id = ?
                )
                AND j.status = 'active'
                AND j.deadline IS NOT NULL
                AND j.deadline <= ?
                AND j.deadline >= date('now')
                ORDER BY j.deadline ASC
            ''', (user_id, deadline_date))
            
            jobs = c.fetchall()
            conn.close()
            
            return [dict(row) for row in jobs]
            
        except Exception as e:
            print(f"Error getting pending jobs: {e}")
            return []
    
    def setup_email_credentials(self, user_id, email_address, app_password):
        """Setup yagmail credentials for a user"""
        try:
            # Test connection
            yag = yagmail.SMTP(email_address, app_password)
            
            # Save settings
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute('''INSERT OR REPLACE INTO email_reminder_settings 
                        (user_id, email_address, is_enabled)
                        VALUES (?, ?, 1)''',
                     (user_id, email_address))
            
            conn.commit()
            conn.close()
            
            print(f"✓ Email credentials setup for user {user_id}")
            return True
            
        except Exception as e:
            print(f"Error setting up email: {e}")
            return False
    
    def send_pending_jobs_email(self, user_id):
        """Send email with pending jobs to user"""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # Get user settings
            c.execute('''SELECT ers.*, s.name, u.email as user_email
                        FROM email_reminder_settings ers
                        JOIN users u ON ers.user_id = u.user_id
                        JOIN students s ON ers.user_id = s.user_id
                        WHERE ers.user_id = ? AND ers.is_enabled = 1''',
                     (user_id,))
            
            settings = c.fetchone()
            
            if not settings:
                print(f"No email settings found for user {user_id}")
                conn.close()
                return False
            
            # Check if enough time has passed since last reminder
            if settings['last_reminder_sent']:
                last_sent = datetime.strptime(settings['last_reminder_sent'], '%Y-%m-%d %H:%M:%S')
                minutes_since = (datetime.now() - last_sent).total_seconds() / 60
                
                if minutes_since < settings['reminder_frequency_minutes']:
                    print(f"Skipping - Last reminder sent {minutes_since:.1f} minutes ago")
                    conn.close()
                    return False
            
            # Get pending jobs
            pending_jobs = self.get_pending_jobs_for_user(
                user_id,
                settings['days_before_deadline']
            )
            
            if not pending_jobs:
                print(f"No pending jobs for user {user_id}")
                conn.close()
                return False
            
            # Get email credentials from email_configurations
            c.execute('''SELECT email_address, app_password 
                        FROM email_configurations 
                        WHERE user_id = ? AND is_enabled = 1''',
                     (user_id,))
            
            email_config = c.fetchone()
            
            if not email_config:
                print(f"No email configuration found for user {user_id}")
                conn.close()
                return False
            
            # Create email content
            email_subject = f"🔔 {len(pending_jobs)} Pending Job Applications - Action Required!"
            
            email_body = self.create_email_html(
                settings['name'],
                pending_jobs,
                settings['days_before_deadline']
            )
            
            # Send email using yagmail
            yag = yagmail.SMTP(email_config['email_address'], email_config['app_password'])
            
            yag.send(
                to=settings['user_email'],
                subject=email_subject,
                contents=email_body
            )
            
            # Update last reminder sent
            c.execute('''UPDATE email_reminder_settings 
                        SET last_reminder_sent = CURRENT_TIMESTAMP 
                        WHERE user_id = ?''',
                     (user_id,))
            
            # Log email
            c.execute('''INSERT INTO email_reminder_logs 
                        (user_id, jobs_count, email_sent, sent_at)
                        VALUES (?, ?, 1, CURRENT_TIMESTAMP)''',
                     (user_id, len(pending_jobs)))
            
            conn.commit()
            conn.close()
            
            print(f"✓ Sent email reminder to {settings['user_email']} with {len(pending_jobs)} jobs")
            return True
            
        except Exception as e:
            print(f"Error sending email: {e}")
            
            # Log error
            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('''INSERT INTO email_reminder_logs 
                            (user_id, jobs_count, email_sent, error_message, sent_at)
                            VALUES (?, 0, 0, ?, CURRENT_TIMESTAMP)''',
                         (user_id, str(e)))
                conn.commit()
                conn.close()
            except:
                pass
            
            return False
    
    def create_email_html(self, user_name, pending_jobs, days_threshold):
        """Create HTML email content"""
        
        # Count urgency levels
        critical = sum(1 for job in pending_jobs if job['days_remaining'] <= 1)
        urgent = sum(1 for job in pending_jobs if 1 < job['days_remaining'] <= 3)
        
        html = f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background-color: #f5f5f5;
                    margin: 0;
                    padding: 20px;
                }}
                .container {{
                    max-width: 650px;
                    margin: 0 auto;
                    background: white;
                    border-radius: 12px;
                    overflow: hidden;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 30px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 28px;
                    font-weight: 700;
                }}
                .header p {{
                    margin: 10px 0 0 0;
                    opacity: 0.9;
                    font-size: 16px;
                }}
                .alert-box {{
                    background: #FEF2F2;
                    border-left: 4px solid #EF4444;
                    padding: 15px 20px;
                    margin: 20px;
                    border-radius: 8px;
                }}
                .alert-box strong {{
                    color: #DC2626;
                    font-size: 16px;
                }}
                .content {{
                    padding: 20px 30px;
                }}
                .greeting {{
                    font-size: 18px;
                    color: #333;
                    margin-bottom: 20px;
                }}
                .stats {{
                    display: flex;
                    gap: 15px;
                    margin: 20px 0;
                }}
                .stat-box {{
                    flex: 1;
                    padding: 15px;
                    border-radius: 8px;
                    text-align: center;
                }}
                .stat-box.critical {{
                    background: #FEE2E2;
                    border: 2px solid #DC2626;
                }}
                .stat-box.urgent {{
                    background: #FEF3C7;
                    border: 2px solid #F59E0B;
                }}
                .stat-box.total {{
                    background: #DBEAFE;
                    border: 2px solid #3B82F6;
                }}
                .stat-number {{
                    font-size: 32px;
                    font-weight: 700;
                    margin-bottom: 5px;
                }}
                .stat-label {{
                    font-size: 14px;
                    color: #666;
                    font-weight: 600;
                }}
                .job-card {{
                    background: #F9FAFB;
                    border: 2px solid #E5E7EB;
                    border-radius: 10px;
                    padding: 20px;
                    margin: 15px 0;
                    transition: all 0.3s;
                }}
                .job-card:hover {{
                    border-color: #667eea;
                    box-shadow: 0 4px 8px rgba(102, 126, 234, 0.2);
                }}
                .job-card.critical {{
                    border-color: #EF4444;
                    background: #FEF2F2;
                }}
                .job-card.urgent {{
                    border-color: #F59E0B;
                    background: #FFFBEB;
                }}
                .job-header {{
                    display: flex;
                    justify-content: space-between;
                    align-items: start;
                    margin-bottom: 12px;
                }}
                .company-name {{
                    font-size: 20px;
                    font-weight: 700;
                    color: #1F2937;
                    margin: 0;
                }}
                .deadline-badge {{
                    padding: 6px 12px;
                    border-radius: 20px;
                    font-size: 13px;
                    font-weight: 700;
                    white-space: nowrap;
                }}
                .deadline-badge.critical {{
                    background: #DC2626;
                    color: white;
                }}
                .deadline-badge.urgent {{
                    background: #F59E0B;
                    color: white;
                }}
                .deadline-badge.normal {{
                    background: #3B82F6;
                    color: white;
                }}
                .position {{
                    font-size: 16px;
                    color: #4B5563;
                    margin: 8px 0;
                    font-weight: 500;
                }}
                .job-details {{
                    display: flex;
                    gap: 20px;
                    flex-wrap: wrap;
                    margin-top: 12px;
                    padding-top: 12px;
                    border-top: 1px solid #E5E7EB;
                }}
                .detail-item {{
                    font-size: 14px;
                    color: #6B7280;
                }}
                .detail-item strong {{
                    color: #374151;
                    font-weight: 600;
                }}
                .apply-button {{
                    display: inline-block;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    color: white;
                    padding: 12px 28px;
                    border-radius: 8px;
                    text-decoration: none;
                    font-weight: 700;
                    margin-top: 15px;
                    font-size: 15px;
                }}
                .apply-button:hover {{
                    background: linear-gradient(135deg, #5568d3, #653a8b);
                }}
                .footer {{
                    background: #F9FAFB;
                    padding: 25px 30px;
                    text-align: center;
                    color: #6B7280;
                    border-top: 1px solid #E5E7EB;
                }}
                .footer-links {{
                    margin-top: 15px;
                }}
                .footer-links a {{
                    color: #667eea;
                    text-decoration: none;
                    margin: 0 10px;
                    font-weight: 600;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🔔 Placement Alert</h1>
                    <p>Pending Job Applications Reminder</p>
                </div>
                
                {f'<div class="alert-box"><strong>⚠️ {critical} Critical Deadline{"s" if critical != 1 else ""} (≤1 day)</strong></div>' if critical > 0 else ''}
                
                <div class="content">
                    <div class="greeting">
                        Hi <strong>{user_name}</strong>,
                    </div>
                    <p style="color: #4B5563; line-height: 1.6;">
                        You have <strong>{len(pending_jobs)}</strong> pending job application{"s" if len(pending_jobs) != 1 else ""} 
                        with deadline{"s" if len(pending_jobs) != 1 else ""} in the next {days_threshold} days. 
                        Don't miss out on these opportunities! ⏰
                    </p>
                    
                    <div class="stats">
                        <div class="stat-box critical">
                            <div class="stat-number">{critical}</div>
                            <div class="stat-label">Critical (≤1 day)</div>
                        </div>
                        <div class="stat-box urgent">
                            <div class="stat-number">{urgent}</div>
                            <div class="stat-label">Urgent (2-3 days)</div>
                        </div>
                        <div class="stat-box total">
                            <div class="stat-number">{len(pending_jobs)}</div>
                            <div class="stat-label">Total Pending</div>
                        </div>
                    </div>
                    
                    <h3 style="color: #1F2937; margin-top: 30px; margin-bottom: 20px;">📋 Pending Applications:</h3>
        """
        
        # Add job cards
        for job in pending_jobs:
            days = job['days_remaining']
            
            if days <= 1:
                urgency_class = 'critical'
                badge_class = 'critical'
                badge_text = f'🔥 {days} day{"s" if days != 1 else ""} left'
            elif days <= 3:
                urgency_class = 'urgent'
                badge_class = 'urgent'
                badge_text = f'⚡ {days} days left'
            else:
                urgency_class = ''
                badge_class = 'normal'
                badge_text = f'📅 {days} days left'
            
            html += f"""
                    <div class="job-card {urgency_class}">
                        <div class="job-header">
                            <h3 class="company-name">{job['company']}</h3>
                            <span class="deadline-badge {badge_class}">{badge_text}</span>
                        </div>
                        <div class="position">{job['position'] or 'Position Available'}</div>
                        <div class="job-details">
                            {f'<div class="detail-item"><strong>💰 CTC:</strong> {job["ctc"]}</div>' if job.get('ctc') else ''}
                            {f'<div class="detail-item"><strong>📍 Location:</strong> {job["location"]}</div>' if job.get('location') else ''}
                            <div class="detail-item"><strong>📅 Deadline:</strong> {job['deadline']}</div>
                            {f'<div class="detail-item"><strong>💼 Type:</strong> {job["job_type"]}</div>' if job.get('job_type') else ''}
                        </div>
                        <a href="http://localhost:5000/jobs" class="apply-button">Apply Now →</a>
                    </div>
            """
        
        html += """
                    <div style="margin-top: 30px; padding: 20px; background: #EFF6FF; border-radius: 8px; border-left: 4px solid #3B82F6;">
                        <p style="margin: 0; color: #1E40AF; font-weight: 600;">
                            💡 <strong>Pro Tip:</strong> Apply early to increase your chances of getting shortlisted!
                        </p>
                    </div>
                </div>
                
                <div class="footer">
                    <p style="margin: 0; font-weight: 600;">PlacementPro - Your Career Success Partner</p>
                    <div class="footer-links">
                        <a href="http://localhost:5000/dashboard">Dashboard</a>
                        <a href="http://localhost:5000/jobs">Browse Jobs</a>
                        <a href="http://localhost:5000/email_settings">Manage Alerts</a>
                    </div>
                    <p style="margin-top: 15px; font-size: 13px;">
                        You're receiving this because you enabled job reminders. 
                        <a href="http://localhost:5000/email_settings" style="color: #667eea;">Manage preferences</a>
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
    
    def schedule_email_reminders(self):
        """Start scheduler for email reminders"""
        try:
            # Get all users with enabled email reminders
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute('''SELECT user_id, reminder_frequency_minutes 
                        FROM email_reminder_settings 
                        WHERE is_enabled = 1''')
            
            users = c.fetchall()
            conn.close()
            
            if not users:
                print("No users with email reminders enabled")
                return
            
            # Schedule job for each user
            for user_id, frequency_minutes in users:
                job_id = f'email_reminder_user_{user_id}'
                
                # Remove existing job if any
                try:
                    self.scheduler.remove_job(job_id)
                except:
                    pass
                
                # Add new job
                self.scheduler.add_job(
                    func=self.send_pending_jobs_email,
                    args=[user_id],
                    trigger=IntervalTrigger(minutes=frequency_minutes),
                    id=job_id,
                    replace_existing=True,
                    max_instances=1
                )
                
                print(f"✓ Scheduled email reminders for user {user_id} (every {frequency_minutes} minutes)")
            
            # Start scheduler if not running
            if not self.scheduler.running:
                self.scheduler.start()
                print("✓ Scheduler started")
            
        except Exception as e:
            print(f"Error scheduling reminders: {e}")
    
    def update_reminder_settings(self, user_id, frequency_minutes=None, days_before_deadline=None, is_enabled=None):
        """Update reminder settings for a user"""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            updates = []
            params = []
            
            if frequency_minutes is not None:
                updates.append('reminder_frequency_minutes = ?')
                params.append(frequency_minutes)
            
            if days_before_deadline is not None:
                updates.append('days_before_deadline = ?')
                params.append(days_before_deadline)
            
            if is_enabled is not None:
                updates.append('is_enabled = ?')
                params.append(is_enabled)
            
            if updates:
                updates.append('updated_at = CURRENT_TIMESTAMP')
                params.append(user_id)
                
                query = f"UPDATE email_reminder_settings SET {', '.join(updates)} WHERE user_id = ?"
                c.execute(query, params)
                conn.commit()
            
            conn.close()
            
            # Reschedule if settings changed
            self.schedule_email_reminders()
            
            return True
            
        except Exception as e:
            print(f"Error updating settings: {e}")
            return False
    
    def stop_scheduler(self):
        """Stop the scheduler"""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown()
                print("✓ Scheduler stopped")
        except Exception as e:
            print(f"Error stopping scheduler: {e}")


# Global instance
notification_system = NotificationSystem()


def start_notification_system():
    """Start the notification system on app startup"""
    print("\n" + "="*60)
    print("STARTING NOTIFICATION SYSTEM")
    print("="*60)
    
    # Initialize database
    notification_system.init_database()
    
    # Schedule email reminders
    notification_system.schedule_email_reminders()
    
    print("✓ Notification system ready")
    print("="*60 + "\n")


if __name__ == '__main__':
    start_notification_system()
    
    # Keep running
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        notification_system.stop_scheduler()
        print("\nNotification system stopped")