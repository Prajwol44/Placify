from flask import Flask, render_template, request, redirect, send_file, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
from datetime import datetime
import PyPDF2
import pdfplumber
import fitz  # PyMuPDF
import re
import openai
import json
import base64
from notification_system import (
    notification_system, 
    start_notification_system
)
from email_job_processor import EmailProcessor
from dotenv import load_dotenv

load_dotenv()


# OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found. Please add it to your .env file")


openai.api_key = OPENAI_API_KEY

# Check OpenAI version
OPENAI_NEW_VERSION = hasattr(openai, 'OpenAI')
if OPENAI_NEW_VERSION:
    client = openai.OpenAI(api_key=OPENAI_API_KEY)


app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'
app.config['UPLOAD_FOLDER'] = 'uploads/resumes'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
ALLOWED_EXTENSIONS = {'pdf'}

# OpenAI API Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found. Please add it to your .env file")

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():
    conn = sqlite3.connect('placement_portal.db')
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Students table - Enhanced with more fields
    c.execute('''CREATE TABLE IF NOT EXISTS students (
        student_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT NOT NULL,
        roll_no TEXT UNIQUE NOT NULL,
        college_id TEXT,
        phone TEXT,
        cgpa REAL,
        skills TEXT,
        linkedin_url TEXT,
        github_url TEXT,
        portfolio_url TEXT,
        address TEXT,
        languages TEXT,
        certifications TEXT,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )''')
    
    # Resumes table - Enhanced with parsed data
    c.execute('''CREATE TABLE IF NOT EXISTS resumes (
        resume_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        filename TEXT NOT NULL,
        file_path TEXT NOT NULL,
        extracted_text TEXT,
        parsed_data TEXT,
        technical_skills TEXT,
        soft_skills TEXT,
        education TEXT,
        experience TEXT,
        projects TEXT,
        certifications TEXT,
        languages TEXT,
        summary TEXT,
        upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_primary BOOLEAN DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )''')
    
    # Jobs table
    c.execute('''CREATE TABLE IF NOT EXISTS jobs (
        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT NOT NULL,
        position TEXT NOT NULL,
        ctc TEXT,
        location TEXT,
        job_type TEXT,
        deadline DATE,
        description TEXT,
        requirements TEXT,
        eligibility TEXT,
        email_date TIMESTAMP,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Applications table
    c.execute('''CREATE TABLE IF NOT EXISTS applications (
        application_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        job_id INTEGER,
        resume_id INTEGER,
        status TEXT DEFAULT 'pending',
        applied_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(user_id),
        FOREIGN KEY (job_id) REFERENCES jobs(job_id),
        FOREIGN KEY (resume_id) REFERENCES resumes(resume_id)
    )''')

    
    conn.commit()
    conn.close()
 

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db():
    conn = sqlite3.connect('placement_portal.db')
    conn.row_factory = sqlite3.Row
    return conn

# ============================================================
# PDF EXTRACTION FUNCTIONS (MULTIPLE METHODS)
# ============================================================

def extract_text_from_pdf(pdf_path):
    """Extract text from PDF using multiple methods for robustness"""
    
    # Method 1: Try PyPDF2
    print("Attempting extraction with PyPDF2...")
    try:
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            text = ''
            for page in pdf_reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + '\n'
            
            if len(text.strip()) > 100:  # If we got substantial text
                print(f"✓ PyPDF2 extracted {len(text)} characters")
                return text.strip()
            else:
                print("✗ PyPDF2 extracted insufficient text")
    except Exception as e:
        print(f"✗ PyPDF2 failed: {e}")
    
    # Method 2: Try pdfplumber (better for complex layouts)
    print("Attempting extraction with pdfplumber...")
    try:
        text = ''
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + '\n'
        
        if len(text.strip()) > 100:
            print(f"✓ pdfplumber extracted {len(text)} characters")
            return text.strip()
        else:
            print("✗ pdfplumber extracted insufficient text")
    except Exception as e:
        print(f"✗ pdfplumber failed: {e}")
    
    # Method 3: Try PyMuPDF (best for scanned PDFs and complex formats)
    print("Attempting extraction with PyMuPDF...")
    try:
        text = ''
        pdf_document = fitz.open(pdf_path)
        for page_num in range(pdf_document.page_count):
            page = pdf_document[page_num]
            page_text = page.get_text()
            if page_text:
                text += page_text + '\n'
        pdf_document.close()
        
        if len(text.strip()) > 100:
            print(f"✓ PyMuPDF extracted {len(text)} characters")
            return text.strip()
        else:
            print("✗ PyMuPDF extracted insufficient text")
    except Exception as e:
        print(f"✗ PyMuPDF failed: {e}")
    
    # If all text extraction methods failed, return empty string
    print("⚠ All text extraction methods failed - PDF might be image-based or encrypted")
    return ''

# ============================================================
# GPT PARSING FUNCTIONS
# ============================================================

def parse_resume_with_gpt(resume_text):
    """Parse resume using OpenAI GPT to extract structured information"""
    
    # If no text provided, return None
    if not resume_text or len(resume_text.strip()) < 50:
        print("⚠ Insufficient text for GPT parsing")
        return None
    
    try:
        prompt = f"""You are an expert resume parser. Extract the following information from this resume and return ONLY a valid JSON object with these exact keys (use null for missing information, and empty arrays [] for missing lists):

{{
    "name": "full name",
    "email": "email address",
    "phone": "phone number",
    "technical_skills": ["list", "of", "technical", "skills"],
    "soft_skills": ["list", "of", "soft", "skills"],
    "education": [
        {{
            "degree": "degree name",
            "institution": "university/college name",
            "year": "graduation year",
            "cgpa": "cgpa or percentage"
        }}
    ],
    "experience": [
        {{
            "company": "company name",
            "role": "job title",
            "duration": "time period",
            "description": "brief description"
        }}
    ],
    "projects": [
        {{
            "name": "project name",
            "description": "brief description",
            "technologies": ["tech1", "tech2"]
        }}
    ],
    "certifications": ["list", "of", "certifications"],
    "languages": ["list", "of", "languages"],
    "linkedin": "linkedin url",
    "github": "github url",
    "portfolio": "portfolio url",
    "summary": "2-3 line professional summary"
}}

IMPORTANT: Return only valid JSON. Use empty arrays [] not null for lists. Use empty strings "" not null for text fields.

Resume text:
{resume_text}

Return ONLY the JSON object, no other text."""

        # Legacy syntax for openai < 1.0.0
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a precise resume parsing assistant. Always return valid JSON with proper arrays and strings, never null values."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=2000
        )
        
        result = response['choices'][0]['message']['content'].strip()
        
        # Remove markdown code blocks if present
        if result.startswith('```json'):
            result = result[7:]
        if result.startswith('```'):
            result = result[3:]
        if result.endswith('```'):
            result = result[:-3]
        
        parsed_data = json.loads(result.strip())
        
        # Ensure all required fields exist with proper defaults
        defaults = {
            'name': '',
            'email': '',
            'phone': '',
            'technical_skills': [],
            'soft_skills': [],
            'education': [],
            'experience': [],
            'projects': [],
            'certifications': [],
            'languages': [],
            'linkedin': '',
            'github': '',
            'portfolio': '',
            'summary': ''
        }
        
        # Merge with defaults
        for key, default_value in defaults.items():
            if key not in parsed_data or parsed_data[key] is None:
                parsed_data[key] = default_value
            # Convert None to empty list for list fields
            elif isinstance(default_value, list) and parsed_data[key] is None:
                parsed_data[key] = []
        
        print(f"✓ GPT successfully parsed resume: {len(parsed_data.get('technical_skills', []))} skills found")
        return parsed_data
    
    except json.JSONDecodeError as e:
        print(f"✗ JSON parsing error: {e}")
        print(f"GPT Response: {result[:500]}")
        return None
    except Exception as e:
        print(f"✗ Error parsing resume with GPT: {e}")
        return None

def parse_resume_with_gpt_from_pdf(pdf_path):
    """If text extraction fails, send PDF directly to GPT-4 Vision"""
    try:
        print("Attempting to parse PDF directly with GPT-4 Vision...")
        
        # Convert first page to image using PyMuPDF
        pdf_document = fitz.open(pdf_path)
        images_base64 = []
        
        # Extract up to 3 pages as images
        for page_num in range(min(3, pdf_document.page_count)):
            page = pdf_document[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better quality
            img_bytes = pix.tobytes("png")
            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
            images_base64.append(img_base64)
        
        pdf_document.close()
        
        # Create message with images
        messages = [
            {
                "role": "system",
                "content": "You are an expert resume parser. Extract information from this resume image and return a JSON object."
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": """Extract the following information from this resume and return ONLY a valid JSON object:
{
    "name": "full name",
    "email": "email address",
    "phone": "phone number",
    "technical_skills": ["list", "of", "technical", "skills"],
    "soft_skills": ["list", "of", "soft", "skills"],
    "education": [{"degree": "", "institution": "", "year": "", "cgpa": ""}],
    "experience": [{"company": "", "role": "", "duration": "", "description": ""}],
    "projects": [{"name": "", "description": "", "technologies": []}],
    "certifications": ["list"],
    "languages": ["list"],
    "linkedin": "url",
    "github": "url",
    "portfolio": "url",
    "summary": "2-3 line summary"
}"""
                    }
                ]
            }
        ]
        
        # Add images
        for img_base64 in images_base64:
            messages[1]["content"].append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_base64}"
                }
            })
        
        response = openai.chat.completions.create(
            model="gpt-4o",  # GPT-4 with vision
            messages=messages,
            max_tokens=2000,
            temperature=0.3
        )
        
        result = response.choices[0].message.content.strip()
        
        # Clean up the response
        if result.startswith('```json'):
            result = result[7:]
        if result.startswith('```'):
            result = result[3:]
        if result.endswith('```'):
            result = result[:-3]
        
        parsed_data = json.loads(result.strip())
        
        # Ensure defaults
        defaults = {
            'name': '', 'email': '', 'phone': '',
            'technical_skills': [], 'soft_skills': [],
            'education': [], 'experience': [], 'projects': [],
            'certifications': [], 'languages': [],
            'linkedin': '', 'github': '', 'portfolio': '', 'summary': ''
        }
        
        for key, default_value in defaults.items():
            if key not in parsed_data or parsed_data[key] is None:
                parsed_data[key] = default_value
        
        print("✓ Successfully parsed PDF with GPT-4 Vision")
        return parsed_data
        
    except Exception as e:
        print(f"✗ GPT-4 Vision parsing failed: {e}")
        return None

# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        roll_no = request.form.get('roll_no')
        college_id = request.form.get('college_id')
        phone = request.form.get('phone')
        cgpa = request.form.get('cgpa')
        
        if not all([email, password, name, roll_no]):
            flash('Please fill all required fields', 'error')
            return redirect(url_for('signup'))
        
        conn = get_db()
        c = conn.cursor()
        
        # Check if user exists
        c.execute('SELECT * FROM users WHERE email = ?', (email,))
        if c.fetchone():
            flash('Email already registered', 'error')
            conn.close()
            return redirect(url_for('signup'))
        
        # Create user
        hashed_password = generate_password_hash(password)
        c.execute('INSERT INTO users (email, password) VALUES (?, ?)',
                  (email, hashed_password))
        user_id = c.lastrowid
        
        # Create student profile
        c.execute('''INSERT INTO students (user_id, name, roll_no, college_id, phone, cgpa)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (user_id, name, roll_no, college_id, phone, cgpa))
        
        conn.commit()
        conn.close()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE email = ?', (email,))
        user = c.fetchone()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['user_id']
            session['email'] = user['email']
            flash('Welcome back!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully', 'success')
    return redirect(url_for('index'))


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    user_id = session['user_id']
    
    # Get student info
    c.execute('SELECT * FROM students WHERE user_id = ?', (user_id,))
    student = c.fetchone()
    
    # Get resume count
    c.execute('SELECT COUNT(*) as count FROM resumes WHERE user_id = ?', (user_id,))
    resume_count = c.fetchone()['count']
    
    # Get job statistics
    c.execute('SELECT COUNT(*) as count FROM applications WHERE user_id = ?', (user_id,))
    applied_count = c.fetchone()['count']
    
    # Get active jobs count
    c.execute('''SELECT COUNT(*) as count FROM jobs 
                 WHERE status = 'active' 
                 AND (deadline IS NULL OR deadline >= date('now'))''')
    active_jobs_count = c.fetchone()['count']
    
    # Get pending jobs (not applied yet)
    c.execute('''SELECT COUNT(*) as count FROM jobs 
                 WHERE job_id NOT IN (SELECT job_id FROM applications WHERE user_id = ?)
                 AND status = 'active' 
                 AND (deadline IS NULL OR deadline >= date('now'))''', (user_id,))
    pending_count = c.fetchone()['count']
    
    # Get critical jobs count (deadline within 3 days)
    c.execute('''SELECT COUNT(*) as count FROM jobs 
                 WHERE status = 'active'
                 AND deadline IS NOT NULL
                 AND julianday(deadline) - julianday('now') <= 3
                 AND julianday(deadline) - julianday('now') >= 0''')
    critical_count = c.fetchone()['count']
    
    # Get urgent jobs count (deadline within 7 days)
    c.execute('''SELECT COUNT(*) as count FROM jobs 
                 WHERE status = 'active'
                 AND deadline IS NOT NULL
                 AND julianday(deadline) - julianday('now') <= 7
                 AND julianday(deadline) - julianday('now') >= 0''')
    urgent_count = c.fetchone()['count']
    
    # Get application status breakdown
    c.execute('''SELECT status, COUNT(*) as count 
                 FROM applications 
                 WHERE user_id = ? 
                 GROUP BY status''', (user_id,))
    application_status = {row['status']: row['count'] for row in c.fetchall()}
    
    # Get recent activity count (applications in last 7 days)
    c.execute('''SELECT COUNT(*) as count FROM applications 
                 WHERE user_id = ? 
                 AND julianday('now') - julianday(applied_date) <= 7''', (user_id,))
    recent_activity = c.fetchone()['count']
    
    # Calculate success rate (if there are any applications)
    success_rate = 0
    if applied_count > 0:
        accepted = application_status.get('accepted', 0)
        success_rate = round((accepted / applied_count) * 100)
    
    conn.close()
    
    stats = {
        'applied': applied_count,
        'pending': pending_count,
        'total_jobs': active_jobs_count,
        'resumes': resume_count,
        'critical': critical_count,
        'urgent': urgent_count,
        'pending_status': application_status.get('pending', 0),
        'accepted': application_status.get('accepted', 0),
        'rejected': application_status.get('rejected', 0),
        'recent_activity': recent_activity,
        'success_rate': success_rate
    }
    
    return render_template('dashboard.html', student=student, stats=stats)

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    user_id = session['user_id']
    
    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone')
        cgpa = request.form.get('cgpa')
        skills = request.form.get('skills')
        linkedin_url = request.form.get('linkedin_url')
        github_url = request.form.get('github_url')
        portfolio_url = request.form.get('portfolio_url')
        
        c.execute('''UPDATE students 
                     SET name = ?, phone = ?, cgpa = ?, skills = ?,
                         linkedin_url = ?, github_url = ?, portfolio_url = ?
                     WHERE user_id = ?''',
                  (name, phone, cgpa, skills, linkedin_url, github_url, 
                   portfolio_url, user_id))
        conn.commit()
        flash('Profile updated successfully!', 'success')
    
    c.execute('SELECT * FROM students WHERE user_id = ?', (user_id,))
    student = c.fetchone()
    
    # Get resumes
    c.execute('SELECT * FROM resumes WHERE user_id = ? ORDER BY upload_date DESC', (user_id,))
    resumes = c.fetchall()
    
    conn.close()
    
    return render_template('profile.html', student=student, resumes=resumes)

@app.route('/upload_resume', methods=['POST'])
def upload_resume():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    if 'resume' not in request.files:
        flash('No file uploaded', 'error')
        return redirect(url_for('profile'))
    
    file = request.files['resume']
    
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('profile'))
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{session['user_id']}_{timestamp}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        print(f"\n{'='*60}")
        print(f"📄 Processing resume: {filename}")
        print(f"{'='*60}\n")
        
        # Step 1: Try multiple text extraction methods
        extracted_text = extract_text_from_pdf(filepath)
        
        parsed_data = None
        
        # Step 2: If text extraction succeeded, parse with GPT
        if extracted_text and len(extracted_text.strip()) > 100:
            print(f"\n✓ Text extraction successful ({len(extracted_text)} characters)")
            print("📤 Sending text to GPT for parsing...\n")
            parsed_data = parse_resume_with_gpt(extracted_text)
        else:
            print("\n⚠ Text extraction failed or insufficient text")
            print("📤 Attempting direct PDF parsing with GPT-4 Vision...\n")
            # Step 3: If text extraction failed, try GPT-4 Vision
            parsed_data = parse_resume_with_gpt_from_pdf(filepath)
            extracted_text = "Extracted using GPT-4 Vision (image-based PDF)"
        
        if parsed_data:
            print("\n✓ Resume parsing successful!")
            print(f"   - Name: {parsed_data.get('name', 'N/A')}")
            print(f"   - Skills: {len(parsed_data.get('technical_skills', []))} technical skills")
            print(f"   - Education: {len(parsed_data.get('education', []))} entries")
            print(f"   - Experience: {len(parsed_data.get('experience', []))} entries")
            
            # Extract specific fields with proper error handling
            technical_skills = ', '.join(parsed_data.get('technical_skills', []) or [])
            soft_skills = ', '.join(parsed_data.get('soft_skills', []) or [])
            education = json.dumps(parsed_data.get('education', []) or [])
            experience = json.dumps(parsed_data.get('experience', []) or [])
            projects = json.dumps(parsed_data.get('projects', []) or [])
            certifications = ', '.join(parsed_data.get('certifications', []) or [])
            languages = ', '.join(parsed_data.get('languages', []) or [])
            summary = parsed_data.get('summary', '') or ''
            
            # Save to database
            conn = get_db()
            c = conn.cursor()
            c.execute('''INSERT INTO resumes 
                        (user_id, filename, file_path, extracted_text, parsed_data,
                         technical_skills, soft_skills, education, experience, 
                         projects, certifications, languages, summary)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (session['user_id'], filename, filepath, extracted_text, 
                       json.dumps(parsed_data), technical_skills, soft_skills,
                       education, experience, projects, certifications, languages, summary))
            
            # Update student profile with extracted info
            if technical_skills:
                c.execute('''UPDATE students 
                           SET skills = ?, linkedin_url = ?, github_url = ?, portfolio_url = ?
                           WHERE user_id = ?''',
                         (technical_skills, 
                          parsed_data.get('linkedin') or None,
                          parsed_data.get('github') or None,
                          parsed_data.get('portfolio') or None,
                          session['user_id']))
            
            conn.commit()
            conn.close()
            
            print(f"\n✓ Resume saved to database")
            print(f"{'='*60}\n")
            
            flash('Resume uploaded and analyzed successfully! 🎉', 'success')
        else:
            print("\n✗ All parsing methods failed")
            print("Saving resume without parsed data...\n")
            
            # Save resume even if parsing failed
            conn = get_db()
            c = conn.cursor()
            c.execute('''INSERT INTO resumes 
                        (user_id, filename, file_path, extracted_text)
                        VALUES (?, ?, ?, ?)''',
                      (session['user_id'], filename, filepath, extracted_text or "Extraction failed"))
            conn.commit()
            conn.close()
            
            flash('Resume uploaded but AI analysis failed. You can try uploading again with a different format.', 'warning')
        
        return redirect(url_for('profile'))
    
    flash('Invalid file type. Please upload a PDF file.', 'error')
    return redirect(url_for('profile'))

@app.route('/delete_resume/<int:resume_id>', methods=['POST'])
def delete_resume(resume_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    conn = get_db()
    c = conn.cursor()
    
    # Get resume info and verify ownership
    c.execute('SELECT * FROM resumes WHERE resume_id = ? AND user_id = ?', 
              (resume_id, session['user_id']))
    resume = c.fetchone()
    
    if not resume:
        flash('Resume not found', 'error')
        conn.close()
        return redirect(url_for('profile'))
    
    # Delete the file from filesystem
    try:
        if os.path.exists(resume['file_path']):
            os.remove(resume['file_path'])
    except Exception as e:
        print(f"Error deleting file: {e}")
    
    # Delete from database
    c.execute('DELETE FROM resumes WHERE resume_id = ?', (resume_id,))
    conn.commit()
    conn.close()
    
    flash('Resume deleted successfully', 'success')
    return redirect(url_for('profile'))

# @app.route('/jobs')
# def jobs():
#     if 'user_id' not in session:
#         return redirect(url_for('login'))
    
#     conn = get_db()
#     c = conn.cursor()
    
#     # Get all active jobs
#     c.execute('''SELECT j.*, 
#                  (SELECT COUNT(*) FROM applications WHERE job_id = j.job_id AND user_id = ?) as applied
#                  FROM jobs j
#                  WHERE j.status = 'active'
#                  ORDER BY j.created_at DESC''', (session['user_id'],))
#     all_jobs = c.fetchall()
    
#     conn.close()
    
#     return render_template('jobs.html', jobs=all_jobs)

@app.route('/apply_job/<int:job_id>', methods=['POST'])
def apply_job(job_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    conn = get_db()
    c = conn.cursor()
    
    # Check if already applied
    c.execute('SELECT * FROM applications WHERE user_id = ? AND job_id = ?',
              (session['user_id'], job_id))
    if c.fetchone():
        flash('Already applied to this job', 'warning')
        conn.close()
        return redirect(url_for('jobs'))
    
    # Get primary resume
    c.execute('SELECT resume_id FROM resumes WHERE user_id = ? ORDER BY upload_date DESC LIMIT 1',
              (session['user_id'],))
    resume = c.fetchone()
    resume_id = resume['resume_id'] if resume else None
    
    # Create application
    c.execute('INSERT INTO applications (user_id, job_id, resume_id) VALUES (?, ?, ?)',
              (session['user_id'], job_id, resume_id))
    conn.commit()
    conn.close()
    
    flash('Application submitted successfully! 🎉', 'success')
    return redirect(url_for('jobs'))

# ============================================================
# CUSTOM JINJA2 FILTERS
# ============================================================

@app.template_filter('from_json')
def from_json_filter(value):
    """Custom filter to parse JSON strings in templates"""
    try:
        if value and value != '[]':
            return json.loads(value)
        return []
    except:
        return []

# ============================================================
# MAIN
# ============================================================



# ============================================================
# EMAIL CONFIGURATION - Add these imports at top
# ============================================================
import imaplib
import email
from email.header import decode_header
from datetime import timedelta
import threading
import time
 
# ============================================================
# EMAIL CONFIGURATION ROUTES
# ============================================================

@app.route('/email_settings')
def email_settings():
    """Email configuration page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Get current configuration
    c.execute('SELECT * FROM email_configurations WHERE user_id = ?', (session['user_id'],))
    config = c.fetchone()
    
    # Get fetch logs
    c.execute('''SELECT * FROM email_fetch_logs 
                 WHERE user_id = ? 
                 ORDER BY fetch_time DESC LIMIT 10''', (session['user_id'],))
    logs = c.fetchall()
    
    # Get student info
    c.execute('SELECT * FROM students WHERE user_id = ?', (session['user_id'],))
    student = c.fetchone()
    
    conn.close()
    
    return render_template('email_settings.html', config=config, logs=logs, student=student)

@app.route('/save_email_config', methods=['POST'])
def save_email_config():
    """Save or update email configuration"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        email_address = request.form.get('email_address')
        app_password = request.form.get('app_password')
        is_enabled = request.form.get('is_enabled') == 'on'
        auto_fetch_enabled = request.form.get('auto_fetch_enabled') == 'on'
        fetch_interval = int(request.form.get('fetch_interval_minutes', 30))
        emails_to_fetch = int(request.form.get('emails_to_fetch', 100))
        filter_keywords = request.form.get('filter_keywords', 'placement,job,recruitment')
        
        if not email_address or not app_password:
            flash('Email and App Password are required', 'error')
            return redirect(url_for('email_settings'))
        
        conn = get_db()
        c = conn.cursor()
        
        # Check if configuration exists
        c.execute('SELECT * FROM email_configurations WHERE user_id = ?', (session['user_id'],))
        existing = c.fetchone()
        
        if existing:
            # Update existing configuration
            c.execute('''UPDATE email_configurations 
                        SET email_address = ?, app_password = ?, is_enabled = ?,
                            auto_fetch_enabled = ?, fetch_interval_minutes = ?,
                            emails_to_fetch = ?, filter_keywords = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = ?''',
                     (email_address, app_password, is_enabled, auto_fetch_enabled,
                      fetch_interval, emails_to_fetch, filter_keywords, session['user_id']))
        else:
            # Insert new configuration
            c.execute('''INSERT INTO email_configurations 
                        (user_id, email_address, app_password, is_enabled,
                         auto_fetch_enabled, fetch_interval_minutes, emails_to_fetch,
                         filter_keywords)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (session['user_id'], email_address, app_password, is_enabled,
                      auto_fetch_enabled, fetch_interval, emails_to_fetch, filter_keywords))
        
        conn.commit()
        conn.close()
        
        flash('Email configuration saved successfully! ✉️', 'success')
        return redirect(url_for('email_settings'))
        
    except Exception as e:
        flash(f'Error saving configuration: {str(e)}', 'error')
        return redirect(url_for('email_settings'))

@app.route('/test_email_connection', methods=['POST'])
def test_email_connection():
    """Test email connection with provided credentials"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        email_address = request.form.get('email_address')
        app_password = request.form.get('app_password')
        
        if not email_address or not app_password:
            return jsonify({'success': False, 'error': 'Email and password required'})
        
        # Try to connect
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(email_address, app_password)
        mail.select('inbox')
        mail.logout()
        
        return jsonify({'success': True, 'message': 'Connection successful! ✓'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Connection failed: {str(e)}'})

@app.route('/fetch_emails_now', methods=['POST'])
def fetch_emails_now():
    """Manually trigger email fetch"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    try:
        user_id = session['user_id']
        batch_size = request.form.get('batch_size', 30, type=int)

        # Initialize EmailProcessor
        processor = EmailProcessor(user_id=user_id, batch_size=batch_size)

        # Run email processing
        processor.run(continuous=False)

        # Get stats
        result = processor.stats

        # return jsonify({
        #     'success': True,
        #     'emails_processed': stats['processed'],
        #     'new_jobs': stats['new_jobs'],
        #     'emails_skipped': stats['skipped'],
        #     'primary_jobs': stats.get('primary_jobs', 0),
        #     'secondary_jobs': stats.get('secondary_jobs', 0)
        # })
 
        if result['success']:
            flash(f"✓ Fetched {result['processed']} emails, found { result['new_jobs']} new jobs!", 'success')
        else:
            flash(f"Error: {result['error']}", 'error')
        
        return redirect(url_for('email_settings'))
        
    except Exception as e:
        flash(f'Error fetching emails: {str(e)}', 'error')
        return redirect(url_for('email_settings'))

@app.route('/toggle_email_config', methods=['POST'])
def toggle_email_config():
    """Toggle email configuration on/off"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        is_enabled = request.json.get('is_enabled', False)
        
        conn = get_db()
        c = conn.cursor()
        c.execute('''UPDATE email_configurations 
                    SET is_enabled = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?''',
                 (is_enabled, session['user_id']))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Configuration updated'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/delete_email_config', methods=['POST'])
def delete_email_config():
    """Delete email configuration"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('DELETE FROM email_configurations WHERE user_id = ?', (session['user_id'],))
        conn.commit()
        conn.close()
        
        flash('Email configuration deleted successfully', 'success')
        return redirect(url_for('email_settings'))
        
    except Exception as e:
        flash(f'Error deleting configuration: {str(e)}', 'error')
        return redirect(url_for('email_settings'))
 
# ============================================================
# ATTACHMENT DOWNLOAD ROUTE
# ============================================================

# @app.route('/download_attachment/<int:job_id>')
# def download_attachment(job_id):
#     """Download job attachment"""
#     if 'user_id' not in session:
#         flash('Please login to download attachments', 'error')
#         return redirect(url_for('login'))
    
#     conn = get_db()
#     c = conn.cursor()
#     c.execute('SELECT attachment_path, attachment_filename FROM jobs WHERE job_id = ?', (job_id,))
#     job = c.fetchone()
#     conn.close()
    
#     if not job or not job['attachment_path']:
#         flash('Attachment not found', 'error')
#         return redirect(url_for('jobs'))
    
#     try:
#         return send_file(
#             job['attachment_path'],
#             as_attachment=True,
#             download_name=job['attachment_filename']
#         )
#     except Exception as e:
#         flash(f'Error downloading file: {str(e)}', 'error')
#         return redirect(url_for('jobs'))

# @app.route('/view_attachment/<int:job_id>')
# def view_attachment(job_id):
#     """View job attachment in browser"""
#     if 'user_id' not in session:
#         flash('Please login to view attachments', 'error')
#         return redirect(url_for('login'))
    
#     conn = get_db()
#     c = conn.cursor()
#     c.execute('SELECT attachment_path, attachment_filename FROM jobs WHERE job_id = ?', (job_id,))
#     job = c.fetchone()
#     conn.close()
    
#     if not job or not job['attachment_path']:
#         flash('Attachment not found', 'error')
#         return redirect(url_for('jobs'))
    
#     try:
#         return send_file(
#             job['attachment_path'],
#             as_attachment=False,
#             download_name=job['attachment_filename']
#         )
#     except Exception as e:
#         flash(f'Error viewing file: {str(e)}', 'error')
#         return redirect(url_for('jobs'))
#         # ------------------------

# Start background task (optional - comment out if not needed)
# threading.Thread(target=auto_fetch_emails_background, daemon=True).start()


# Add these routes to your app.py file

import threading
from flask import Response, stream_with_context
import json
import time

# Global dictionary to track monitoring sessions
monitoring_sessions = {}

@app.route('/bulk_fetch_emails', methods=['POST'])
def bulk_fetch_emails():
    """Fetch and process emails in bulk"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        user_id = session['user_id']
        batch_size = request.json.get('batch_size', 50)
        
        # Validate batch size
        if batch_size < 10 or batch_size > 500:
            return jsonify({'success': False, 'error': 'Batch size must be between 10 and 500'}), 400
        
        # Initialize processor
        processor = EmailProcessor(user_id=user_id, batch_size=batch_size)
        processor.run(continuous=False)
        
        # Return results
        return jsonify({
            'success': True,
            'emails_processed': processor.stats['processed'],
            'new_jobs': processor.stats['new_jobs'],
            'skipped': processor.stats['skipped'],
            'primary_jobs': processor.stats.get('primary_jobs', 0),
            'secondary_jobs': processor.stats.get('secondary_jobs', 0)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/start_live_monitoring', methods=['POST'])
def start_live_monitoring():
    """Start live email monitoring for a user"""
    print("\n[LOG] Received request to start live monitoring")

    if 'user_id' not in session:
        print("[ERROR] Unauthorized request — no user_id in session")
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    user_id = session['user_id']
    print(f"[LOG] Starting live monitoring for user_id: {user_id}")

    # Check if already monitoring
    if user_id in monitoring_sessions and monitoring_sessions[user_id].get('active'):
        print(f"[WARN] Monitoring already active for user_id: {user_id}")
        return jsonify({'success': False, 'error': 'Monitoring already active'}), 400
    
    # Initialize monitoring session
    monitoring_sessions[user_id] = {
        'active': True,
        'started_at': datetime.now(),
        'stats': {'processed': 0, 'new_jobs': 0, 'checks': 0}
    }
    print(f"[LOG] Monitoring session initialized for user_id: {user_id}")

    # Start monitoring in background thread
    def monitor_thread():
        print(f"[THREAD-START] Live monitoring thread started for user_id: {user_id}")
        processor = EmailProcessor(user_id=user_id, batch_size=30)

        print("[LOG] Attempting to connect to email server...")
        if not processor.connect_to_email():
            print("[ERROR] Email connection failed. Stopping monitoring.")
            monitoring_sessions[user_id]['active'] = False
            return
        
        print("[LOG] Email connection established successfully.")
        last_check = datetime.now()
        
        while monitoring_sessions[user_id].get('active', False):
            try:
                monitoring_sessions[user_id]['stats']['checks'] += 1
                print(f"[LOOP] Check #{monitoring_sessions[user_id]['stats']['checks']} for user_id: {user_id}")

                # Fetch new emails
                new_emails = processor.fetch_new_emails_since(last_check)
                print(f"[LOG] Fetched {len(new_emails)} new emails since last check at {last_check}")

                if new_emails:
                    for i, email_data in enumerate(new_emails, start=1):
                        try:
                            print(f"[PROCESS] Processing email #{i}")
                            processor.process_email(email_data)
                            monitoring_sessions[user_id]['stats']['processed'] += 1
                            print(f"[SUCCESS] Email #{i} processed successfully.")
                        except Exception as e:
                            print(f"[ERROR] Failed to process email #{i}: {e}")
                            processor.stats['skipped'] += 1
                    
                    monitoring_sessions[user_id]['stats']['new_jobs'] = processor.stats['new_jobs']
                    last_check = datetime.now()
                    print(f"[LOG] Stats updated — Processed: {monitoring_sessions[user_id]['stats']['processed']}, New Jobs: {monitoring_sessions[user_id]['stats']['new_jobs']}")
                
                # Wait 5 seconds before next check
                print("[WAIT] Sleeping for 5 seconds before next check...\n")
                time.sleep(5)
                
            except Exception as e:
                print(f"[EXCEPTION] Error during monitoring loop: {e}")
                time.sleep(5)
        
        # Clean up
        print(f"[THREAD-END] Monitoring stopped for user_id: {user_id}. Cleaning up connection.")
        if processor.imap:
            try:
                processor.imap.close()
                processor.imap.logout()
                print("[CLEANUP] IMAP connection closed and logged out successfully.")
            except Exception as e:
                print(f"[CLEANUP-ERROR] Error closing IMAP connection: {e}")
    
    thread = threading.Thread(target=monitor_thread, daemon=True)
    thread.start()
    print(f"[LOG] Background thread started for user_id: {user_id}")

    return jsonify({'success': True, 'message': 'Live monitoring started'})


@app.route('/stop_live_monitoring', methods=['POST'])
def stop_live_monitoring():
    """Stop live email monitoring"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    user_id = session['user_id']
    
    if user_id in monitoring_sessions:
        monitoring_sessions[user_id]['active'] = False
        stats = monitoring_sessions[user_id].get('stats', {})
        return jsonify({'success': True, 'message': 'Monitoring stopped', 'stats': stats})
    
    return jsonify({'success': False, 'error': 'No active monitoring session'}), 400


@app.route('/monitoring_status', methods=['GET'])
def monitoring_status():
    """Get current monitoring status"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    user_id = session['user_id']
    
    if user_id in monitoring_sessions and monitoring_sessions[user_id].get('active'):
        session_data = monitoring_sessions[user_id]
        return jsonify({
            'success': True,
            'active': True,
            'started_at': session_data['started_at'].strftime('%Y-%m-%d %H:%M:%S'),
            'stats': session_data['stats'],
            'duration': str(datetime.now() - session_data['started_at']).split('.')[0]
        })
    
    return jsonify({'success': True, 'active': False})


@app.route('/monitoring_stream')
def monitoring_stream():
    """Server-Sent Events stream for live monitoring updates"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    def generate():
        user_id = session['user_id']
        last_stats = {}
        
        while True:
            if user_id in monitoring_sessions and monitoring_sessions[user_id].get('active'):
                stats = monitoring_sessions[user_id].get('stats', {})
                
                # Only send update if stats changed
                if stats != last_stats:
                    data = json.dumps({
                        'active': True,
                        'stats': stats,
                        'timestamp': datetime.now().strftime('%H:%M:%S')
                    })
                    yield f"data: {data}\n\n"
                    last_stats = stats.copy()
            else:
                yield f"data: {json.dumps({'active': False})}\n\n"
            
            time.sleep(2)  # Update every 2 seconds
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')


# ============================================================
# NEW ROUTES TO ADD TO app.py
# Add these routes to your existing app.py file
# ============================================================

# @app.route('/api/job-details/<int:job_id>')
# def get_job_details(job_id):
#     """Get detailed information about a specific job"""
#     if 'user_id' not in session:
#         return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
#     try:
#         conn = get_db()
#         c = conn.cursor()
        
#         # Get job details
#         c.execute('''
#             SELECT j.*, 
#                    CASE WHEN a.application_id IS NOT NULL THEN 1 ELSE 0 END as applied
#             FROM jobs j
#             LEFT JOIN applications a ON j.job_id = a.job_id AND a.user_id = ?
#             WHERE j.job_id = ?
#         ''', (session['user_id'], job_id))
        
#         job = c.fetchone()
#         conn.close()
        
#         if not job:
#             return jsonify({'success': False, 'error': 'Job not found'}), 404
        
#         # Convert to dict
#         job_dict = dict(job)
        
#         return jsonify({
#             'success': True,
#             'job': job_dict
#         })
        
#     except Exception as e:
#         print(f"Error fetching job details: {e}")
#         return jsonify({'success': False, 'error': str(e)}), 500


# Add these new routes and update existing ones in your app.py



# FIXED VERSION - Add these routes to your app.py

@app.route('/api/job-details/<int:job_id>')
def get_job_details(job_id):
    """Get detailed job information including email subject and attachments - FIXED VERSION"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # FIXED: First get the job details
        c.execute('''
            SELECT j.*,
                   CASE WHEN a.application_id IS NOT NULL THEN 1 ELSE 0 END as applied
            FROM jobs j
            LEFT JOIN applications a ON j.job_id = a.job_id AND a.user_id = ?
            WHERE j.job_id = ?
        ''', (session['user_id'], job_id))
        
        job_row = c.fetchone()
        
        if not job_row:
            conn.close()
            return jsonify({'success': False, 'error': 'Job not found'}), 404
        
        # Convert to dict
        job = dict(job_row)
        
        # FIXED: Now get the email details for this job
        c.execute('''
            SELECT id, subject, from_email, email_date
            FROM processed_emails
            WHERE job_id = ?
            ORDER BY email_date DESC
            LIMIT 1
        ''', (job_id,))
        
        email_row = c.fetchone()
        
        if email_row:
            job['email_subject'] = email_row['subject']
            job['from_email'] = email_row['from_email']
            job['email_date'] = email_row['email_date']
            email_id = email_row['id']
            
            # FIXED: Get attachments for this email
            c.execute('''
                SELECT filename, file_path, file_size, content_type
                FROM email_attachments
                WHERE email_id = ?
                ORDER BY created_at DESC
            ''', (email_id,))
            
            attachments = c.fetchall()
            
            if attachments:
                # Get the first attachment (or you could return all)
                first_attachment = attachments[0]
                job['attachment_filename'] = first_attachment['filename']
                job['attachment_path'] = first_attachment['file_path']
                job['attachment_size'] = first_attachment['file_size']
                job['attachment_type'] = first_attachment['content_type']
                
                # If you want all attachments, uncomment this:
                # job['all_attachments'] = [dict(att) for att in attachments]
            else:
                job['attachment_filename'] = None
                job['attachment_path'] = None
        else:
            job['email_subject'] = None
            job['from_email'] = None
            job['email_date'] = None
            job['attachment_filename'] = None
            job['attachment_path'] = None
        
        conn.close()
        
        return jsonify({
            'success': True,
            'job': job
        })
        
    except Exception as e:
        print(f"Error fetching job details: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/view-attachment/<int:job_id>')
def view_attachment(job_id):
    """View email attachment for a job"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Get email_id from job_id
        c.execute('SELECT id FROM processed_emails WHERE job_id = ? LIMIT 1', (job_id,))
        email_row = c.fetchone()
        
        if not email_row:
            conn.close()
            flash('No email found for this job', 'error')
            return redirect(url_for('jobs'))
        
        email_id = email_row['id']
        
        # Get attachment info
        c.execute('''
            SELECT file_path, filename
            FROM email_attachments
            WHERE email_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        ''', (email_id,))
        
        attachment = c.fetchone()
        conn.close()
        
        if not attachment:
            flash('Attachment not found', 'error')
            return redirect(url_for('jobs'))
        
        file_path = attachment['file_path']
        
        if not os.path.exists(file_path):
            flash('Attachment file not found on server', 'error')
            return redirect(url_for('jobs'))
        
        # Return file for viewing (opens in browser)
        return send_file(
            file_path,
            as_attachment=False,
            download_name=attachment['filename']
        )
        
    except Exception as e:
        print(f"Error viewing attachment: {e}")
        flash('Error viewing attachment', 'error')
        return redirect(url_for('jobs'))


@app.route('/download-attachment/<int:job_id>')
def download_attachment(job_id):
    """Download email attachment for a job"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Get email_id from job_id
        c.execute('SELECT id FROM processed_emails WHERE job_id = ? LIMIT 1', (job_id,))
        email_row = c.fetchone()
        
        if not email_row:
            conn.close()
            flash('No email found for this job', 'error')
            return redirect(url_for('jobs'))
        
        email_id = email_row['id']
        
        # Get attachment info
        c.execute('''
            SELECT file_path, filename
            FROM email_attachments
            WHERE email_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        ''', (email_id,))
        
        attachment = c.fetchone()
        conn.close()
        
        if not attachment:
            flash('Attachment not found', 'error')
            return redirect(url_for('jobs'))
        
        file_path = attachment['file_path']
        
        if not os.path.exists(file_path):
            flash('Attachment file not found on server', 'error')
            return redirect(url_for('jobs'))
        
        # Return file for download
        return send_file(
            file_path,
            as_attachment=True,
            download_name=attachment['filename']
        )
        
    except Exception as e:
        print(f"Error downloading attachment: {e}")
        flash('Error downloading attachment', 'error')
        return redirect(url_for('jobs'))


# TESTING ENDPOINT - Use this to debug
@app.route('/debug/job/<int:job_id>')
def debug_job(job_id):
    """Debug endpoint to check job, email, and attachment relationships"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    conn = get_db()
    c = conn.cursor()
    
    # Get job
    c.execute('SELECT * FROM jobs WHERE job_id = ?', (job_id,))
    job = dict(c.fetchone()) if c.fetchone() else None
    
    c.execute('SELECT * FROM jobs WHERE job_id = ?', (job_id,))
    job = dict(c.fetchone()) if c.fetchone() else None
    
    # Get processed_email
    c.execute('SELECT * FROM processed_emails WHERE job_id = ?', (job_id,))
    email = dict(c.fetchone()) if c.fetchone() else None
    
    # Get attachments if email exists
    attachments = []
    if email:
        c.execute('SELECT * FROM email_attachments WHERE email_id = ?', (email['id'],))
        attachments = [dict(row) for row in c.fetchall()]
    
    conn.close()
    
    return jsonify({
        'job': job,
        'email': email,
        'attachments': attachments,
        'relationship': {
            'job_id': job_id,
            'has_email': email is not None,
            'email_id': email['id'] if email else None,
            'has_attachments': len(attachments) > 0,
            'attachment_count': len(attachments)
        }
    })

@app.route('/api/resume-recommendation/<int:job_id>')
def get_resume_recommendation(job_id):
    """Get AI-powered resume recommendation for a specific job"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        user_id = session['user_id']
        conn = get_db()
        c = conn.cursor()
        
        # Get job details
        c.execute('''
            SELECT company, position, description, requirements, eligibility, ctc, location
            FROM jobs WHERE job_id = ?
        ''', (job_id,))
        job = c.fetchone()
        
        if not job:
            conn.close()
            return jsonify({'success': False, 'error': 'Job not found'}), 404
        
        # Get all user's resumes
        c.execute('''
            SELECT resume_id, filename, extracted_text, technical_skills, 
                   soft_skills, education, experience, projects, summary
            FROM resumes 
            WHERE user_id = ?
            ORDER BY is_primary DESC, upload_date DESC
        ''', (user_id,))
        resumes = c.fetchall()
        conn.close()
        
        if not resumes:
            return jsonify({
                'success': False, 
                'error': 'No resumes found. Please upload your resume first.'
            }), 404
        
        # Prepare job description for GPT
        job_description = f"""
Company: {job['company'] or 'Not specified'}
Position: {job['position'] or 'Not specified'}
Package: {job['ctc'] or 'Not specified'}
Location: {job['location'] or 'Not specified'}
Eligibility: {job['eligibility'] or 'Not specified'}

Job Description:
{job['description'] or 'Not provided'}

Requirements:
{job['requirements'] or 'Not provided'}
"""
        
        # Prepare resumes summary
        resumes_summary = []
        for idx, resume in enumerate(resumes, 1):
            resume_info = f"""
Resume {idx}: {resume['filename']}
Technical Skills: {resume['technical_skills'] or 'Not extracted'}
Education: {resume['education'] or 'Not extracted'}
Experience: {resume['experience'] or 'Not extracted'}
Projects: {resume['projects'] or 'Not extracted'}
Summary: {resume['summary'] or 'Not available'}
"""
            resumes_summary.append(resume_info)
        
        # Create GPT prompt
        prompt = f"""You are an expert career advisor and resume consultant. Analyze the following job posting and the candidate's resumes to provide a comprehensive recommendation.

JOB POSTING:
{job_description}

CANDIDATE'S RESUMES:
{''.join(resumes_summary)}

Please provide a detailed recommendation that includes:

1. **Best Resume to Use**: Which resume (by number and filename) is most suitable for this job and why?

2. **Match Analysis**: 
   - What are the key matching points between the best resume and job requirements?
   - What percentage match would you estimate (0-100%)?

3. **Gap Analysis**:
   - What skills or qualifications are required but missing from the resume?
   - What experiences could be highlighted better?

4. **Recommended Modifications**:
   - Specific sections to enhance or modify
   - Keywords to add based on the job description
   - Achievements to emphasize

5. **Application Strategy**:
   - Cover letter key points
   - How to present your candidacy effectively
   - Red flags to address (if any)

6. **Improvement Suggestions**:
   - Skills to acquire before applying
   - Projects or certifications that would strengthen the application
   - How to tailor the resume specifically for this role

Please be specific, actionable, and honest in your assessment. Format your response in a clear, structured way with headers and bullet points where appropriate.
"""
        
        # Call OpenAI API
        try:
            if OPENAI_NEW_VERSION:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are an expert career advisor specializing in resume optimization and job matching."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=2000
                )
                recommendation = response.choices[0].message.content
            else:
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "You are an expert career advisor specializing in resume optimization and job matching."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=2000
                )
                recommendation = response['choices'][0]['message']['content']
            
            return jsonify({
                'success': True,
                'recommendation': recommendation,
                'resumes_analyzed': len(resumes)
            })
            
        except Exception as e:
            print(f"OpenAI API Error: {e}")
            return jsonify({
                'success': False,
                'error': f'AI recommendation service error: {str(e)}'
            }), 500
        
    except Exception as e:
        print(f"Error generating recommendation: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/apply-job/<int:job_id>', methods=['POST'])
def apply_job_new(job_id):
    """Apply to a job (new version - returns JSON)"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        user_id = session['user_id']
        conn = get_db()
        c = conn.cursor()
        
        # Check if already applied
        c.execute('''
            SELECT application_id FROM applications 
            WHERE user_id = ? AND job_id = ?
        ''', (user_id, job_id))
        
        if c.fetchone():
            conn.close()
            return jsonify({'success': False, 'error': 'Already applied to this job'}), 400
        
        # Get primary resume or latest resume
        c.execute('''
            SELECT resume_id FROM resumes 
            WHERE user_id = ? 
            ORDER BY is_primary DESC, upload_date DESC 
            LIMIT 1
        ''', (user_id,))
        
        resume = c.fetchone()
        
        if not resume:
            conn.close()
            return jsonify({
                'success': False, 
                'error': 'No resume found. Please upload your resume first.'
            }), 400
        
        # Create application
        c.execute('''
            INSERT INTO applications (user_id, job_id, resume_id, status, applied_date)
            VALUES (?, ?, ?, 'pending', CURRENT_TIMESTAMP)
        ''', (user_id, job_id, resume['resume_id']))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Application submitted successfully'
        })
        
    except Exception as e:
        print(f"Error applying to job: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500



# ============================================================
# FIXED JOBS ROUTE - Replace your existing /jobs route with this
# ============================================================

# @app.route('/jobs')
# def jobs():
#     """Display all jobs - Latest first, with urgency indicators"""
#     if 'user_id' not in session:
#         return redirect(url_for('login'))
    
#     try:
#         conn = get_db()
#         c = conn.cursor()
        
#         # FIXED QUERY - Orders by created_at DESC (latest first)
#         c.execute('''
#             SELECT j.*, 
#                    CASE WHEN a.application_id IS NOT NULL THEN 1 ELSE 0 END as applied,
#                    CASE 
#                        WHEN j.deadline IS NULL THEN 3
#                        WHEN julianday(j.deadline) - julianday('now') <= 3 THEN 0
#                        WHEN julianday(j.deadline) - julianday('now') <= 7 THEN 1
#                        ELSE 2
#                    END as urgency_level,
#                    CAST((julianday(j.deadline) - julianday('now')) as INTEGER) as days_remaining,
#                    pe.subject as email_subject,
#                    pe.from_email
#             FROM jobs j
#             LEFT JOIN applications a ON j.job_id = a.job_id AND a.user_id = ?
#             LEFT JOIN processed_emails pe ON j.job_id = pe.job_id
#             WHERE j.status = 'active'
#             ORDER BY j.created_at DESC
#         ''', (session['user_id'],))
        
#         # Convert rows to dictionaries
#         jobs_list = []
#         for row in c.fetchall():
#             job_dict = dict(row)
#             jobs_list.append(job_dict)
        
#         conn.close()
        
#         # Debug output
#         print(f"✓ Loaded {len(jobs_list)} jobs")
#         if jobs_list:
#             print(f"  Latest job: {jobs_list[0].get('company')} (created: {jobs_list[0].get('created_at')})")
#             print(f"  Oldest job: {jobs_list[-1].get('company')} (created: {jobs_list[-1].get('created_at')})")
        
#         return render_template('jobs.html', jobs=jobs_list)
        
#     except Exception as e:
#         import traceback
#         print(f"ERROR in /jobs route: {str(e)}")
#         print(traceback.format_exc())
#         flash(f'Error loading jobs: {str(e)}', 'error')
#         return redirect(url_for('dashboard'))



# ============================================================
# FIXED JOBS ROUTE - Replace your existing /jobs route with this
# ============================================================

@app.route('/jobs')
def jobs():
    """Display all jobs - Latest first, with urgency indicators"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # FIXED QUERY - Orders by created_at DESC (latest first)
        c.execute('''
            SELECT j.*, 
                   CASE WHEN a.application_id IS NOT NULL THEN 1 ELSE 0 END as applied,
                   CASE 
                       WHEN j.deadline IS NULL THEN 3
                       WHEN julianday(j.deadline) - julianday('now') <= 3 THEN 0
                       WHEN julianday(j.deadline) - julianday('now') <= 7 THEN 1
                       ELSE 2
                   END as urgency_level,
                   CAST((julianday(j.deadline) - julianday('now')) as INTEGER) as days_remaining,
                   pe.subject as email_subject,
                   pe.from_email
            FROM jobs j
            LEFT JOIN applications a ON j.job_id = a.job_id AND a.user_id = ?
            LEFT JOIN processed_emails pe ON j.job_id = pe.job_id
            WHERE j.status = 'active'
            ORDER BY j.created_at DESC
        ''', (session['user_id'],))
        
        # Convert rows to dictionaries
        jobs_list = []
        for row in c.fetchall():
            job_dict = dict(row)
            jobs_list.append(job_dict)
        
        conn.close()
        
        # Debug output
        print(f"✓ Loaded {len(jobs_list)} jobs")
        if jobs_list:
            print(f"  Latest job: {jobs_list[0].get('company')} (created: {jobs_list[0].get('created_at')})")
            print(f"  Oldest job: {jobs_list[-1].get('company')} (created: {jobs_list[-1].get('created_at')})")
        
        return render_template('jobs.html', jobs=jobs_list)
        
    except Exception as e:
        import traceback
        print(f"ERROR in /jobs route: {str(e)}")
        print(traceback.format_exc())
        flash(f'Error loading jobs: {str(e)}', 'error')
        return redirect(url_for('dashboard'))


# ============================================================
# UPDATED EMAIL REMINDERS ROUTE - With Pending Jobs List
# ============================================================

 
# @app.route('/jobs')
# def jobs():
#     if 'user_id' not in session:
#         return redirect(url_for('login'))
    
#     try:
#         conn = get_db()
#         c = conn.cursor()
        
#         # Modified query to prioritize jobs by deadline
#         c.execute('''
#             SELECT j.*, 
#                    CASE WHEN a.application_id IS NOT NULL THEN 1 ELSE 0 END as applied,
#                    CASE 
#                        WHEN j.deadline IS NULL THEN 3
#                        WHEN julianday(j.deadline) - julianday('now') <= 3 THEN 0
#                        WHEN julianday(j.deadline) - julianday('now') <= 7 THEN 1
#                        ELSE 2
#                    END as urgency_level,
#                    CAST((julianday(j.deadline) - julianday('now')) as INTEGER) as days_remaining
#             FROM jobs j
#             LEFT JOIN applications a ON j.job_id = a.job_id AND a.user_id = ?
#             WHERE j.status = 'active'
#             ORDER BY urgency_level ASC, j.deadline ASC, j.created_at DESC
#         ''', (session['user_id'],))
        
#         # Convert rows to dictionaries properly
#         jobs_list = []
#         for row in c.fetchall():
#             job_dict = dict(row)
#             jobs_list.append(job_dict)
        
#         conn.close()
        
#         print(f"DEBUG: Loaded {len(jobs_list)} jobs")  # Debug line
#         if jobs_list:
#             print(f"DEBUG: First job keys: {jobs_list[0].keys()}")  # Debug line
#             print(f"DEBUG: Sample job data: {jobs_list[0]}")  # Debug line
        
#         return render_template('jobs.html', jobs=jobs_list)
        
#     except Exception as e:
#         import traceback
#         print(f"ERROR in /jobs route: {str(e)}")
#         print(traceback.format_exc())
#         flash(f'Error loading jobs: {str(e)}', 'error')
#         return redirect(url_for('dashboard'))
    

# ============================================================
# FIXED JOBS ROUTE - Replace your existing /jobs route with this
# ============================================================

# @app.route('/jobs')
# def jobs():
#     """Display all jobs - Latest first, with urgency indicators"""
#     if 'user_id' not in session:
#         return redirect(url_for('login'))
    
#     try:
#         conn = get_db()
#         c = conn.cursor()
        
#         # FIXED QUERY - Orders by created_at DESC (latest first)
#         c.execute('''
#             SELECT j.*, 
#                    CASE WHEN a.application_id IS NOT NULL THEN 1 ELSE 0 END as applied,
#                    CASE 
#                        WHEN j.deadline IS NULL THEN 3
#                        WHEN julianday(j.deadline) - julianday('now') <= 3 THEN 0
#                        WHEN julianday(j.deadline) - julianday('now') <= 7 THEN 1
#                        ELSE 2
#                    END as urgency_level,
#                    CAST((julianday(j.deadline) - julianday('now')) as INTEGER) as days_remaining,
#                    pe.subject as email_subject,
#                    pe.from_email
#             FROM jobs j
#             LEFT JOIN applications a ON j.job_id = a.job_id AND a.user_id = ?
#             LEFT JOIN processed_emails pe ON j.job_id = pe.job_id
#             WHERE j.status = 'active'
#             ORDER BY j.created_at DESC
#         ''', (session['user_id'],))
        
#         # Convert rows to dictionaries
#         jobs_list = []
#         for row in c.fetchall():
#             job_dict = dict(row)
#             jobs_list.append(job_dict)
        
#         conn.close()
        
#         # Debug output
#         print(f"✓ Loaded {len(jobs_list)} jobs")
#         if jobs_list:
#             print(f"  Latest job: {jobs_list[0].get('company')} (created: {jobs_list[0].get('created_at')})")
#             print(f"  Oldest job: {jobs_list[-1].get('company')} (created: {jobs_list[-1].get('created_at')})")
        
#         return render_template('jobs.html', jobs=jobs_list)
        
#     except Exception as e:
#         import traceback
#         print(f"ERROR in /jobs route: {str(e)}")
#         print(traceback.format_exc())
#         flash(f'Error loading jobs: {str(e)}', 'error')
#         return redirect(url_for('dashboard'))


# ============================================================
# UPDATED EMAIL REMINDERS ROUTE - With Pending Jobs List
# ============================================================

 
# ============================================================
# UPDATED EMAIL REMINDERS ROUTE - With Pending Jobs List
# ============================================================



# ============================================================
# NOTIFICATION ROUTES - Add these to app.py
# ============================================================


@app.route('/api/notifications')
def get_notifications():
    """Get notifications for current user"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        unread_only = request.args.get('unread_only', 'false').lower() == 'true'
        limit = int(request.args.get('limit', 20))
        
        notifications = notification_system.get_user_notifications(
            session['user_id'],
            unread_only=unread_only,
            limit=limit
        )
        
        unread_count = notification_system.get_unread_count(session['user_id'])
        
        return jsonify({
            'success': True,
            'notifications': notifications,
            'unread_count': unread_count
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
def mark_notification_read(notification_id):
    """Mark a notification as read"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        success = notification_system.mark_notification_read(
            notification_id,
            session['user_id']
        )
        
        return jsonify({'success': success})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/notifications/mark-all-read', methods=['POST'])
def mark_all_notifications_read():
    """Mark all notifications as read"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        success = notification_system.mark_all_notifications_read(session['user_id'])
        return jsonify({'success': success})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500



@app.route('/save-email-reminder-settings', methods=['POST'])
def save_email_reminder_settings():
    """Save email reminder settings"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        is_enabled = request.form.get('is_enabled') == 'on'
        frequency_minutes = int(request.form.get('frequency_minutes', 1440))
        days_before = int(request.form.get('days_before_deadline', 3))
        
        conn = get_db()
        c = conn.cursor()
        
        # Check if settings exist
        c.execute('SELECT * FROM email_reminder_settings WHERE user_id = ?', 
                 (session['user_id'],))
        existing = c.fetchone()
        
        # Get user email
        c.execute('SELECT email FROM users WHERE user_id = ?', (session['user_id'],))
        user_email = c.fetchone()['email']
        
        if existing:
            # Update
            c.execute('''UPDATE email_reminder_settings 
                        SET is_enabled = ?,
                            reminder_frequency_minutes = ?,
                            days_before_deadline = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = ?''',
                     (is_enabled, frequency_minutes, days_before, session['user_id']))
        else:
            # Insert
            c.execute('''INSERT INTO email_reminder_settings 
                        (user_id, email_address, is_enabled, reminder_frequency_minutes, days_before_deadline)
                        VALUES (?, ?, ?, ?, ?)''',
                     (session['user_id'], user_email, is_enabled, frequency_minutes, days_before))
        
        conn.commit()
        conn.close()
        
        # Update scheduler
        notification_system.schedule_email_reminders()
        
        # Show user-friendly message
        if frequency_minutes < 60:
            freq_text = f"{frequency_minutes} minute{'s' if frequency_minutes != 1 else ''}"
        else:
            hours = frequency_minutes // 60
            freq_text = f"{hours} hour{'s' if hours != 1 else ''}"
        
        flash(f'✓ Email reminders set to every {freq_text}!', 'success')
        return redirect(url_for('email_reminders_settings'))
        
    except Exception as e:
        flash(f'Error saving settings: {str(e)}', 'error')
        return redirect(url_for('email_reminders_settings'))


@app.route('/send-test-reminder', methods=['POST'])
def send_test_reminder():
    """Send a test reminder email"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        success = notification_system.send_pending_jobs_email(session['user_id'])
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Test email sent successfully! Check your inbox.'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'No pending jobs or email not configured'
            })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
 

@app.route('/notification-settings')
def notification_settings():
    """Browser notification settings page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db()
    c = conn.cursor()
    
    # Get student info
    c.execute('SELECT * FROM students WHERE user_id = ?', (session['user_id'],))
    student = c.fetchone()
    
    conn.close()
    
    return render_template('notification_settings.html', student=student)


@app.route('/api/trigger-test-notification', methods=['POST'])
def trigger_test_notification():
    """API endpoint to trigger a test notification"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        data = request.json
        notification_type = data.get('type', 'info')
        title = data.get('title', 'Test Notification')
        message = data.get('message', 'This is a test notification')
        
        # Create a notification in database
        notification_system.create_notification(
            session['user_id'],
            notification_type,
            title,
            message
        )
        
        return jsonify({'success': True, 'message': 'Notification created'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# UPDATED EMAIL REMINDERS ROUTE - With Pending Jobs List
# ============================================================

@app.route('/email-reminders')
def email_reminders_settings():
    """Email reminder settings page with pending jobs display"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        conn = get_db()
        c = conn.cursor()
        
        # Get current settings
        c.execute('''SELECT * FROM email_reminder_settings 
                    WHERE user_id = ?''', (session['user_id'],))
        settings = c.fetchone()
        
        # Get recent logs
        c.execute('''SELECT * FROM email_reminder_logs 
                    WHERE user_id = ? 
                    ORDER BY sent_at DESC LIMIT 10''', (session['user_id'],))
        logs = c.fetchall()
        
        # Get student info
        c.execute('SELECT * FROM students WHERE user_id = ?', (session['user_id'],))
        student = c.fetchone()
        
        # Get pending jobs with details
        days_threshold = settings['days_before_deadline'] if settings else 3
        
        c.execute('''
            SELECT j.*,
                   CAST((julianday(j.deadline) - julianday('now')) as INTEGER) as days_remaining,
                   CASE 
                       WHEN julianday(j.deadline) - julianday('now') <= 1 THEN 'critical'
                       WHEN julianday(j.deadline) - julianday('now') <= 3 THEN 'urgent'
                       ELSE 'normal'
                   END as urgency,
                   pe.subject as email_subject,
                   pe.from_email
            FROM jobs j
            LEFT JOIN processed_emails pe ON j.job_id = pe.job_id
            WHERE j.job_id NOT IN (
                SELECT job_id FROM applications WHERE user_id = ?
            )
            AND j.status = 'active'
            AND j.deadline IS NOT NULL
            AND j.deadline <= date('now', '+' || ? || ' days')
            AND j.deadline >= date('now')
            ORDER BY j.deadline ASC, j.created_at DESC
        ''', (session['user_id'], days_threshold))
        
        pending_jobs = c.fetchall()
        
        conn.close()
        
        return render_template(
            'email_reminders.html',
            settings=settings,
            logs=logs,
            student=student,
            pending_count=len(pending_jobs),
            pending_jobs=pending_jobs,
            days_threshold=days_threshold
        )
        
    except Exception as e:
        import traceback
        print(f"ERROR in /email-reminders route: {str(e)}")
        print(traceback.format_exc())
        flash(f'Error loading settings: {str(e)}', 'error')
        return redirect(url_for('dashboard'))

if __name__ == '__main__':
    init_db()
    
    # Start notification system
    start_notification_system()
    
    app.run(debug=False, port=5000)


