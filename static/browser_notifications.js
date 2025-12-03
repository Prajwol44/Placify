/**
 * Browser Push Notifications System
 * Adds to base.html for site-wide notifications
 */

class BrowserNotificationManager {
    constructor() {
        this.permission = Notification.permission;
        this.checkInterval = 30000; // Check every 30 seconds
        this.lastNotificationId = null;
        this.notificationSound = null;
        this.init();
    }
    
    async init() {
        console.log('🔔 Initializing Browser Notifications...');
        
        // Check if browser supports notifications
        if (!('Notification' in window)) {
            console.warn('❌ Browser does not support notifications');
            return;
        }
        
        // Show permission request UI if not granted
        if (this.permission === 'default') {
            this.showPermissionPrompt();
        } else if (this.permission === 'granted') {
            console.log('✅ Notification permission already granted');
            this.startMonitoring();
        } else {
            console.log('⊘ Notification permission denied');
        }
        
        // Create notification sound
        this.createNotificationSound();
    }
    
    showPermissionPrompt() {
        // Create a nice UI prompt instead of browser default
        const promptHtml = `
            <div id="notificationPrompt" style="
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: linear-gradient(135deg, #667eea, #764ba2);
                color: white;
                padding: 1.5rem 2rem;
                border-radius: 16px;
                box-shadow: 0 10px 40px rgba(102, 126, 234, 0.4);
                z-index: 10000;
                max-width: 400px;
                animation: slideInUp 0.3s ease-out;
            ">
                <div style="display: flex; align-items: start; gap: 1rem;">
                    <div style="font-size: 2.5rem;">🔔</div>
                    <div style="flex: 1;">
                        <h3 style="margin: 0 0 0.5rem 0; font-size: 1.1rem; font-weight: 700;">
                            Enable Job Alerts
                        </h3>
                        <p style="margin: 0 0 1rem 0; font-size: 0.95rem; opacity: 0.95; line-height: 1.4;">
                            Get instant browser notifications for new jobs and urgent deadlines!
                        </p>
                        <div style="display: flex; gap: 0.75rem;">
                            <button onclick="browserNotifications.requestPermission()" style="
                                background: white;
                                color: #667eea;
                                border: none;
                                padding: 0.6rem 1.5rem;
                                border-radius: 8px;
                                font-weight: 700;
                                cursor: pointer;
                                font-size: 0.95rem;
                                transition: all 0.3s;
                            " onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform='translateY(0)'">
                                Enable Alerts
                            </button>
                            <button onclick="browserNotifications.dismissPrompt()" style="
                                background: rgba(255, 255, 255, 0.2);
                                color: white;
                                border: none;
                                padding: 0.6rem 1.5rem;
                                border-radius: 8px;
                                font-weight: 600;
                                cursor: pointer;
                                font-size: 0.95rem;
                            ">
                                Maybe Later
                            </button>
                        </div>
                    </div>
                    <button onclick="browserNotifications.dismissPrompt()" style="
                        background: none;
                        border: none;
                        color: white;
                        font-size: 1.5rem;
                        cursor: pointer;
                        padding: 0;
                        line-height: 1;
                        opacity: 0.7;
                        transition: opacity 0.3s;
                    " onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.7'">
                        ×
                    </button>
                </div>
            </div>
            
            <style>
                @keyframes slideInUp {
                    from {
                        transform: translateY(100px);
                        opacity: 0;
                    }
                    to {
                        transform: translateY(0);
                        opacity: 1;
                    }
                }
            </style>
        `;
        
        document.body.insertAdjacentHTML('beforeend', promptHtml);
    }
    
    async requestPermission() {
        try {
            const permission = await Notification.requestPermission();
            this.permission = permission;
            
            if (permission === 'granted') {
                console.log('✅ Notification permission granted!');
                this.showSuccessMessage();
                this.dismissPrompt();
                this.startMonitoring();
                
                // Show a test notification
                this.showTestNotification();
            } else {
                console.log('⊘ Notification permission denied');
                this.showDeniedMessage();
            }
        } catch (error) {
            console.error('Error requesting permission:', error);
        }
    }
    
    dismissPrompt() {
        const prompt = document.getElementById('notificationPrompt');
        if (prompt) {
            prompt.style.animation = 'slideOutDown 0.3s ease-out';
            setTimeout(() => prompt.remove(), 300);
        }
    }
    
    showSuccessMessage() {
        const message = `
            <div style="
                position: fixed;
                top: 100px;
                right: 20px;
                background: linear-gradient(135deg, #10B981, #059669);
                color: white;
                padding: 1rem 1.5rem;
                border-radius: 12px;
                box-shadow: 0 10px 30px rgba(16, 185, 129, 0.4);
                z-index: 10001;
                animation: slideInRight 0.3s ease-out;
                display: flex;
                align-items: center;
                gap: 0.75rem;
            ">
                <span style="font-size: 1.5rem;">✅</span>
                <span style="font-weight: 600;">Notifications Enabled!</span>
            </div>
            
            <style>
                @keyframes slideInRight {
                    from { transform: translateX(400px); opacity: 0; }
                    to { transform: translateX(0); opacity: 1; }
                }
                @keyframes slideOutDown {
                    from { transform: translateY(0); opacity: 1; }
                    to { transform: translateY(100px); opacity: 0; }
                }
            </style>
        `;
        
        document.body.insertAdjacentHTML('beforeend', message);
        setTimeout(() => {
            const msg = document.querySelector('[style*="slideInRight"]').parentElement;
            msg.style.animation = 'slideOutDown 0.3s ease-out';
            setTimeout(() => msg.remove(), 300);
        }, 3000);
    }
    
    showDeniedMessage() {
        const message = `
            <div style="
                position: fixed;
                top: 100px;
                right: 20px;
                background: linear-gradient(135deg, #EF4444, #DC2626);
                color: white;
                padding: 1rem 1.5rem;
                border-radius: 12px;
                box-shadow: 0 10px 30px rgba(239, 68, 68, 0.4);
                z-index: 10001;
                animation: slideInRight 0.3s ease-out;
            ">
                <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem;">
                    <span style="font-size: 1.5rem;">⚠️</span>
                    <span style="font-weight: 700;">Notifications Blocked</span>
                </div>
                <p style="margin: 0; font-size: 0.9rem; opacity: 0.95;">
                    Enable them in browser settings to get alerts
                </p>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', message);
        setTimeout(() => {
            const msg = document.querySelectorAll('[style*="slideInRight"]');
            msg[msg.length - 1].parentElement.style.animation = 'slideOutDown 0.3s ease-out';
            setTimeout(() => msg[msg.length - 1].parentElement.remove(), 300);
        }, 5000);
    }
    
    showTestNotification() {
        this.showNotification({
            title: '🎉 Notifications Enabled!',
            body: 'You\'ll now receive alerts for new jobs and urgent deadlines',
            icon: '🔔',
            tag: 'test-notification'
        });
    }
    
    createNotificationSound() {
        // Create a subtle notification sound using Web Audio API
        try {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            const audioContext = new AudioContext();
            
            this.notificationSound = () => {
                const oscillator = audioContext.createOscillator();
                const gainNode = audioContext.createGain();
                
                oscillator.connect(gainNode);
                gainNode.connect(audioContext.destination);
                
                oscillator.frequency.value = 800;
                oscillator.type = 'sine';
                
                gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
                gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5);
                
                oscillator.start(audioContext.currentTime);
                oscillator.stop(audioContext.currentTime + 0.5);
            };
        } catch (error) {
            console.warn('Could not create notification sound:', error);
        }
    }
    
    playSound() {
        if (this.notificationSound) {
            try {
                this.notificationSound();
            } catch (error) {
                console.warn('Could not play sound:', error);
            }
        }
    }
    
    showNotification({ title, body, icon, tag, data = {} }) {
        if (this.permission !== 'granted') {
            console.log('⊘ Cannot show notification - permission not granted');
            return;
        }
        
        const options = {
            body: body,
            icon: icon === '🔔' ? '/static/icon-bell.png' : (icon || '/static/icon-job.png'),
            badge: '/static/badge.png',
            tag: tag || 'job-notification',
            requireInteraction: false,
            silent: false,
            data: data,
            vibrate: [200, 100, 200],
            actions: [
                { action: 'view', title: 'View Jobs', icon: '/static/icon-view.png' },
                { action: 'dismiss', title: 'Dismiss', icon: '/static/icon-close.png' }
            ]
        };
        
        // For emojis as icons, use a data URL
        if (icon && icon.length <= 2) {
            options.icon = this.emojiToDataURL(icon);
        }
        
        const notification = new Notification(title, options);
        
        // Play sound
        this.playSound();
        
        // Handle notification click
        notification.onclick = (event) => {
            event.preventDefault();
            window.focus();
            
            if (data.url) {
                window.location.href = data.url;
            } else {
                window.location.href = '/jobs';
            }
            
            notification.close();
        };
        
        // Auto close after 10 seconds
        setTimeout(() => notification.close(), 10000);
        
        return notification;
    }
    
    emojiToDataURL(emoji) {
        const canvas = document.createElement('canvas');
        canvas.width = 128;
        canvas.height = 128;
        const ctx = canvas.getContext('2d');
        
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, 128, 128);
        
        ctx.font = '96px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(emoji, 64, 64);
        
        return canvas.toDataURL();
    }
    
    async startMonitoring() {
        console.log('🔄 Starting notification monitoring...');
        
        // Initial check
        await this.checkForNewNotifications();
        
        // Set up periodic checking
        this.monitoringInterval = setInterval(() => {
            this.checkForNewNotifications();
        }, this.checkInterval);
        
        console.log(`✅ Monitoring every ${this.checkInterval / 1000} seconds`);
    }
    
    stopMonitoring() {
        if (this.monitoringInterval) {
            clearInterval(this.monitoringInterval);
            console.log('⏹️ Monitoring stopped');
        }
    }
    
    async checkForNewNotifications() {
        try {
            const response = await fetch('/api/notifications?unread_only=true&limit=5');
            const data = await response.json();
            
            if (!data.success) return;
            
            const notifications = data.notifications;
            
            // Show notifications for new items
            notifications.forEach(notif => {
                // Skip if we've already shown this notification
                if (this.lastNotificationId && notif.notification_id <= this.lastNotificationId) {
                    return;
                }
                
                // Determine icon based on type
                let icon = '🔔';
                if (notif.type === 'critical') icon = '🔥';
                else if (notif.type === 'urgent') icon = '⚡';
                else if (notif.type === 'new_job') icon = '💼';
                
                // Show browser notification
                this.showNotification({
                    title: notif.title,
                    body: notif.message,
                    icon: icon,
                    tag: `notification-${notif.notification_id}`,
                    data: {
                        notification_id: notif.notification_id,
                        job_id: notif.job_id,
                        url: notif.job_id ? `/jobs#job-${notif.job_id}` : '/jobs'
                    }
                });
            });
            
            // Update last notification ID
            if (notifications.length > 0) {
                this.lastNotificationId = Math.max(...notifications.map(n => n.notification_id));
            }
            
        } catch (error) {
            console.error('Error checking notifications:', error);
        }
    }
    
    // Manual trigger method
    triggerNotification(type, title, message, jobId = null) {
        const icons = {
            'critical': '🔥',
            'urgent': '⚡',
            'new_job': '💼',
            'deadline': '⏰',
            'success': '✅',
            'info': '💡'
        };
        
        this.showNotification({
            title: title,
            body: message,
            icon: icons[type] || '🔔',
            tag: `manual-${Date.now()}`,
            data: {
                job_id: jobId,
                url: jobId ? `/jobs#job-${jobId}` : '/jobs'
            }
        });
    }
}

// Initialize browser notifications when DOM is ready
let browserNotifications;

document.addEventListener('DOMContentLoaded', function() {
    // Only initialize if user is logged in
    const isLoggedIn = document.querySelector('.navbar-menu a[href*="dashboard"]');
    
    if (isLoggedIn) {
        browserNotifications = new BrowserNotificationManager();
        
        // Make it globally accessible
        window.browserNotifications = browserNotifications;
        
        console.log('✅ Browser Notifications Ready');
    }
});

// Cleanup on page unload
window.addEventListener('beforeunload', function() {
    if (browserNotifications) {
        browserNotifications.stopMonitoring();
    }
});