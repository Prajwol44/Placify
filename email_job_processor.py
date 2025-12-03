"""
Enhanced Email Job Processor - FINAL VERSION WITH ATTACHMENTS
- Fixed database error with null company/position
- Skip non-job emails (workshops, guest lectures, competitions)
- Track processed thread emails to avoid duplicates
- Better validation and error handling
- ADDED: Attachment tracking and database storage
- ADDED: Link attachments to jobs via processed_emails
"""

import sqlite3
import os
import json
import re
from datetime import datetime, timedelta
import hashlib
import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import PyPDF2
import pdfplumber
import fitz
from dotenv import load_dotenv

load_dotenv()

try:
    import easyocr
    EASYOCR_AVAILABLE = True
except:
    EASYOCR_AVAILABLE = False

try:
    from docx import Document
    DOCX_AVAILABLE = True
except:
    DOCX_AVAILABLE = False

from PIL import Image
import openai

# Configuration
DB_PATH = 'placement_portal.db'
ATTACHMENTS_DIR = 'uploads/email_attachments'
PRIMARY_SENDER = "spr@thapar.edu"
TARGET_GROUP = "CampusNotice2026"
SUBJECT_KEYWORDS = ["internship", "campus", "campus notice", "internship", "placement", "registration","job"]
MIN_NLP_CONFIDENCE = 0.4
MAX_THREAD_EMAILS = 20
MAX_CONTENT_LENGTH = 15000

# OpenAI API Key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found. Please add it to your .env file")

# Check OpenAI version
OPENAI_NEW_VERSION = hasattr(openai, 'OpenAI')
if OPENAI_NEW_VERSION:
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

# Non-job keywords to skip
NON_JOB_KEYWORDS = [
    "workshop", "seminar", "webinar", "guest lecture", "guest speaker",
    "competition", "case study competition", "hackathon announcement",
    "interview prep", "interview preparation", "preparation guide",
    "questions shared", "study material", "resources shared"
]

JOB_RELATED_KEYWORDS = [
    "job", "internship", "position", "role", "opening", "vacancy",
    "recruitment", "hiring", "placement", "opportunity",
    "ctc", "salary", "package", "compensation", "stipend", "lpa",
    "apply", "application", "deadline", "eligibility", "cgpa",
    "interview", "assessment", "jd","offer","designation","position","process", "test", "exam", "screening"
]


class EmailProcessor:
    def __init__(self, user_id=None, batch_size=30):
        self.user_id = user_id
        self.batch_size = batch_size
        self.email_credentials = None
        self.imap = None
        self.stats = {'processed': 0, 'new_jobs': 0, 'skipped': 0}
        self.ocr_reader = None
        self.processed_threads = set()
        self.processed_message_ids = set()  # Track individual messages
        
        if EASYOCR_AVAILABLE:
            try:
                self.ocr_reader = easyocr.Reader(['en'])
            except:
                pass
    
    def log(self, msg, level='INFO'):
        """Clean logging"""
        prefix = {'INFO': '→', 'SUCCESS': '✓', 'ERROR': '✗', 'SKIP': '⊘'}
        print(f"{prefix.get(level, '→')} {msg}")
    
    def load_credentials(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            if not self.user_id:
                c.execute("""SELECT user_id, email_address, app_password, imap_server, imap_port
                            FROM email_configurations WHERE is_enabled = 1 LIMIT 1""")
            else:
                c.execute("""SELECT user_id, email_address, app_password, imap_server, imap_port
                            FROM email_configurations WHERE user_id = ? AND is_enabled = 1""", (self.user_id,))
            
            result = c.fetchone()
            conn.close()
            
            if not result:
                raise Exception("No enabled email configuration found")
            
            self.user_id = result[0]
            self.email_credentials = {
                'email': result[1],
                'password': result[2],
                'imap_server': result[3] or 'imap.gmail.com',
                'imap_port': result[4] or 993
            }
            
            self.log(f"Loaded credentials for: {self.email_credentials['email']}", 'SUCCESS')
            return True
            
        except Exception as e:
            self.log(f"Error loading credentials: {e}", 'ERROR')
            return False
    
    def connect_to_email(self):
        try:
            if not self.email_credentials:
                if not self.load_credentials():
                    return False
            
            self.imap = imaplib.IMAP4_SSL(
                self.email_credentials['imap_server'],
                self.email_credentials['imap_port']
            )
            self.imap.login(self.email_credentials['email'], self.email_credentials['password'])
            self.log("Connected to email server", 'SUCCESS')
            return True
            
        except Exception as e:
            self.log(f"Connection failed: {e}", 'ERROR')
            return False
    
    def is_non_job_email(self, subject, body):
        """Check if email is a workshop/competition/prep material"""
        combined = f"{subject} {body}".lower()
        
        for keyword in NON_JOB_KEYWORDS:
            if keyword in combined:
                return True, keyword
        
        return False, None
    
    def calculate_nlp_confidence(self, text):
        text_lower = text.lower()
        matches = sum(1 for keyword in JOB_RELATED_KEYWORDS if keyword in text_lower)
        confidence = matches / len(JOB_RELATED_KEYWORDS)
        
        strong_indicators = ["apply", "deadline", "ctc","jd","designation","offer","company" "eligibility", "interview"]
        if sum(1 for ind in strong_indicators if ind in text_lower) >= 1:
            confidence = min(1.0, confidence + 0.2)
        
        return confidence
    
    def should_process_email(self, from_addr, to_addr, cc_addr, subject, body_preview):
        if 'TESTING' in subject.upper():print(body_preview)
        from_email = re.search(r'<(.+?)>', from_addr)
        from_email = from_email.group(1) if from_email else from_addr
        from_email = from_email.strip().lower()
        
        to_cc_combined = f"{to_addr or ''} {cc_addr or ''}".lower()
        
        # Check if non-job email
        is_non_job, keyword = self.is_non_job_email(subject, body_preview)
        if is_non_job:
            return False, f"non_job_email_{keyword}"
        
        # Primary source check
        if from_email == PRIMARY_SENDER.lower():
            if TARGET_GROUP.lower() in to_cc_combined:
                return True, "primary_source_group"
            
            subject_lower = subject.lower()
            if any(keyword in subject_lower for keyword in SUBJECT_KEYWORDS):
                return True, "primary_source_keyword"
        
        # NLP check for other emails
        combined_text = f"{subject} {body_preview}"
        confidence = self.calculate_nlp_confidence(combined_text)
        
        if confidence >= MIN_NLP_CONFIDENCE:
            return True, f"nlp_confidence_{confidence:.2f}"
        
        return False, f"low_confidence_{confidence:.2f}"
    
    def get_thread_id(self, email_data):
        thread_id = None
        
        if email_data.get('message_id'):
            if email_data.get('references'):
                refs = email_data['references'].split()
                if refs:
                    thread_id = refs[0]
            elif email_data.get('in_reply_to'):
                thread_id = email_data['in_reply_to']
            else:
                thread_id = email_data['message_id']
        
        if not thread_id:
            subject = email_data['subject']
            clean_subject = re.sub(r'^(Re:|Fwd:|RE:|FW:|Fw:)\s*', '', subject, flags=re.IGNORECASE).strip()
            thread_id = f"thread_{hashlib.md5(clean_subject.encode()).hexdigest()[:16]}"
        
        return thread_id
    
    def fetch_email_thread(self, email_data):
        """Fetch thread emails with LIMIT and duplicate check"""
        thread_emails = []
        
        # Add current email if not already processed
        if email_data['message_id'] not in self.processed_message_ids:
            thread_emails.append(email_data)
            self.processed_message_ids.add(email_data['message_id'])
        
        try:
            subject = email_data['subject']
            clean_subject = re.sub(r'^(Re:|Fwd:|RE:|FW:|Fw:)\s*', '', subject, flags=re.IGNORECASE).strip()
            
            # Only search for threads if it's a reply
            if not subject.lower().startswith(('re:', 'fwd:')):
                return thread_emails
            
            sender_email = email_data.get('from', '').split('<')[-1].strip('>')
            
            self.imap.select('INBOX')
            
            search_query = f'(FROM "{sender_email}")'
            
            try:
                status, messages = self.imap.search(None, search_query)
                if status == 'OK' and messages[0]:
                    email_ids = messages[0].split()
                    email_ids = email_ids[-20:]  # Last 20 emails max
                    
                    for email_id in email_ids:
                        if len(thread_emails) >= MAX_THREAD_EMAILS:
                            break
                        
                        try:
                            status, msg_data = self.imap.fetch(email_id, '(RFC822)')
                            if status == 'OK' and msg_data[0]:
                                email_message = email.message_from_bytes(msg_data[0][1])
                                thread_email_data = self.parse_email_basic(email_message)
                                
                                if thread_email_data:
                                    # Skip if already processed
                                    if thread_email_data['message_id'] in self.processed_message_ids:
                                        continue
                                    
                                    if clean_subject.lower() in thread_email_data['subject'].lower():
                                        thread_emails.append(thread_email_data)
                                        self.processed_message_ids.add(thread_email_data['message_id'])
                        except:
                            continue
            except Exception as e:
                self.log(f"Thread search error: {str(e)[:50]}", 'SKIP')
            
            thread_emails.sort(key=lambda x: x.get('date', datetime.now()))
            
            if len(thread_emails) > 1:
                self.log(f"Found {len(thread_emails)} thread emails")
            
            return thread_emails
            
        except Exception as e:
            self.log(f"Thread fetch error: {str(e)[:50]}", 'SKIP')
            return thread_emails
    
    def parse_email_basic(self, email_message):
        try:
            subject = self.decode_header_value(email_message.get('Subject', ''))
            from_addr = self.decode_header_value(email_message.get('From', ''))
            to_addr = self.decode_header_value(email_message.get('To', ''))
            cc_addr = self.decode_header_value(email_message.get('Cc', ''))
            date_str = email_message.get('Date')
            message_id = email_message.get('Message-ID', '')
            references = email_message.get('References', '')
            in_reply_to = email_message.get('In-Reply-To', '')
            
            try:
                email_date = parsedate_to_datetime(date_str) if date_str else datetime.now()
            except:
                email_date = datetime.now()
            
            body = ""
            attachments = []
            
            if email_message.is_multipart():
                for part in email_message.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get('Content-Disposition', ''))
                    
                    if 'attachment' in content_disposition:
                        filename = part.get_filename()
                        if filename:
                            attachments.append({
                                'filename': self.decode_header_value(filename),
                                'data': part.get_payload(decode=True),
                                'content_type': content_type
                            })
                    elif content_type == 'text/plain' and 'attachment' not in content_disposition:
                        try:
                            body += part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        except:
                            pass
            else:
                try:
                    body = email_message.get_payload(decode=True).decode('utf-8', errors='ignore')
                except:
                    body = str(email_message.get_payload())
            
            return {
                'subject': subject,
                'from': from_addr,
                'to': to_addr,
                'cc': cc_addr,
                'date': email_date,
                'body': body[:5000],
                'attachments': attachments,
                'message_id': message_id,
                'references': references,
                'in_reply_to': in_reply_to
            }
            
        except Exception as e:
            return None
    
    def decode_header_value(self, value):
        if not value:
            return ''
        try:
            decoded_parts = decode_header(value)
            decoded_string = ''
            for part, encoding in decoded_parts:
                if isinstance(part, bytes):
                    decoded_string += part.decode(encoding or 'utf-8', errors='ignore')
                else:
                    decoded_string += str(part)
            return decoded_string
        except:
            return str(value)
    
    def extract_all_content(self, thread_emails):
        """Extract content with limits"""
        all_text = []
        all_attachments = []
        all_urls = []
        
        for idx, email_data in enumerate(thread_emails):
            if idx == 0:
                all_text.append(f"Subject: {email_data['subject']}")
                all_text.append(f"Body: {email_data['body'][:2000]}")
            else:
                all_text.append(f"\n--- Thread Email {idx} ---")
                all_text.append(f"Subject: {email_data['subject']}")
                all_text.append(f"Body: {email_data['body'][:1000]}")
            
            for attachment in email_data.get('attachments', [])[:5]:
                all_attachments.append(attachment)
            
            urls = re.findall(r'https?://[^\s<>"{}\\|\\^`\[\]]+', email_data.get('body', ''))
            all_urls.extend(urls[:10])
        
        relevant_attachments = []
        
        for attachment in all_attachments[:10]:
            filename = attachment['filename']
            file_ext = os.path.splitext(filename)[1].lower()
            
            if file_ext in ['.pdf', '.docx', '.doc', '.txt']:
                safe_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
                attachment_path = os.path.join(ATTACHMENTS_DIR, safe_filename)
                
                try:
                    with open(attachment_path, 'wb') as f:
                        f.write(attachment['data'])
                    
                    extracted_text = self.extract_text_from_attachment(attachment_path, file_ext)
                    
                    if extracted_text:
                        extracted_text = extracted_text[:5000]
                        all_text.append(f"\n--- {filename} ---")
                        all_text.append(extracted_text)
                        
                        relevant_attachments.append({
                            'filename': filename,
                            'path': attachment_path,
                            'extracted_text': extracted_text
                        })
                        self.log(f"Extracted {len(extracted_text)} chars from {filename}")
                
                except Exception as e:
                    self.log(f"Attachment error: {filename[:30]}", 'SKIP')
        
        combined_content = '\n'.join(all_text)
        
        if len(combined_content) > MAX_CONTENT_LENGTH:
            combined_content = combined_content[:MAX_CONTENT_LENGTH] + "\n...[content truncated]"
        
        self.log(f"Content: {len(combined_content)} chars, Attachments: {len(relevant_attachments)}, URLs: {len(set(all_urls))}")
        
        return combined_content, relevant_attachments, list(set(all_urls))[:20]
    
    def extract_text_from_attachment(self, file_path, file_ext):
        try:
            if file_ext == '.pdf':
                return self.extract_text_from_pdf(file_path)
            elif file_ext in ['.docx', '.doc']:
                if DOCX_AVAILABLE:
                    doc = Document(file_path)
                    return '\n'.join([p.text for p in doc.paragraphs])[:5000]
            elif file_ext == '.txt':
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()[:5000]
        except:
            pass
        return ""
    
    def extract_text_from_pdf(self, pdf_path):
        try:
            text = ''
            pdf_document = fitz.open(pdf_path)
            for page_num in range(min(5, pdf_document.page_count)):
                page = pdf_document[page_num]
                text += page.get_text()
            pdf_document.close()
            return text[:5000]
        except:
            pass
        
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ''
                for page in list(pdf_reader.pages)[:5]:
                    text += page.extract_text()
                return text[:5000]
        except:
            pass
        
        return ""
    
    def call_gpt(self, prompt):
        """OpenAI API call - compatible with both versions"""
        try:
            if OPENAI_NEW_VERSION:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are an expert at extracting job details. Return only valid JSON. NEVER include company=null jobs. Skip workshops, competitions, guest lectures."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=1000
                )
                return response.choices[0].message.content.strip()
            else:
                response = openai.ChatCompletion.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are an expert at extracting job details. Return only valid JSON. NEVER include company=null jobs. Skip workshops, competitions, guest lectures."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=1000
                )
                return response['choices'][0]['message']['content'].strip()
        
        except Exception as e:
            raise Exception(f"GPT API error: {str(e)}")
    
    def extract_job_info_with_gpt(self, content, urls):
        """Extract job information - handles multiple jobs"""
        try:
            urls_section = ""
            if urls:
                urls_section = "\nApplication URLs:\n" + "\n".join(urls[:5])
            
            prompt = f"""Extract ONLY actual job/internship postings. SKIP workshops, competitions, guest lectures, interview prep materials.

Content:
{content}
{urls_section}

STRICT RULES:
1. SKIP if email is about:
   - Workshop, seminar, webinar
   - Guest lecture, guest speaker
   - Competition, case study competition
   - Interview preparation, study material, questions shared
   
2. ONLY extract if it's a real job/internship with company hiring

3. company must NOT be null - if no company name, set is_job_posting=false

4. If MULTIPLE jobs → return ARRAY
   If SINGLE job → return OBJECT

JSON Format:
{{
    "company": "company name (REQUIRED, not null)",
    "position": "job title or null",
    "ctc": "salary or 'To be discussed' or null",
    "location": "location or null",
    "job_type": "Full-time/Internship/Part-time or null",
    "deadline": "YYYY-MM-DD or null",
    "test_date": "YYYY-MM-DD or null",
    "interview_date": "YYYY-MM-DD or null",
    "description": "description or null",
    "eligibility": "eligibility or null",
    "apply_link": "URL or null",
    "is_job_posting": true or false
}}

is_job_posting=true ONLY if:
- Company name exists (not null)
- It's a real hiring opportunity (not workshop/competition)
- Has eligibility OR test_date OR apply_link"""

            
            print("***"*50)
            print("\n\n\n\nprompt ",prompt)           
            result = self.call_gpt(prompt)
            print("\n\n\n\nresult ",result)
            print("***"*50)
            
            # Clean JSON
            result = result.strip()
            if result.startswith('```json'):
                result = result[7:]
            if result.startswith('```'):
                result = result[3:]
            if result.endswith('```'):
                result = result[:-3]
            
            result = result.strip()
            
            # Parse JSON
            parsed = json.loads(result)
            
            # Convert to list
            if isinstance(parsed, dict):
                jobs_list = [parsed]
            elif isinstance(parsed, list):
                jobs_list = parsed
            else:
                return []
            
            # Filter valid jobs - company must not be null
            valid_jobs = []
            for job_data in jobs_list:
                if job_data.get('is_job_posting', False) and job_data.get('company'):
                    valid_jobs.append(job_data)
                    self.log(f"Job found: {job_data.get('company')} - {job_data.get('position') or 'Position TBD'}", 'SUCCESS')
            
            return valid_jobs if valid_jobs else None
            
        except Exception as e:
            self.log(f"GPT error: {str(e)[:100]}", 'ERROR')
            return None
    
    # NEW METHOD: Save attachment to database
    def save_attachment_to_db(self, email_id, filename, file_path):
        """Save attachment information to database"""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # Get file size
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None
            
            # Determine content type
            content_type = None
            if filename.lower().endswith('.pdf'):
                content_type = 'application/pdf'
            elif filename.lower().endswith('.docx'):
                content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            elif filename.lower().endswith('.doc'):
                content_type = 'application/msword'
            elif filename.lower().endswith(('.jpg', '.jpeg')):
                content_type = 'image/jpeg'
            elif filename.lower().endswith('.png'):
                content_type = 'image/png'
            
            c.execute('''
                INSERT INTO email_attachments (email_id, filename, file_path, file_size, content_type)
                VALUES (?, ?, ?, ?, ?)
            ''', (email_id, filename, file_path, file_size, content_type))
            
            conn.commit()
            conn.close()
            
            self.log(f"Saved attachment to DB: {filename}", 'SUCCESS')
            return True
            
        except Exception as e:
            self.log(f"Error saving attachment to DB: {e}", 'ERROR')
            return False
    
    def create_job_in_database(self, job_data, email_data, attachments):
        """Create job - skip if company or position is null"""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            company = job_data.get('company', '').strip() if job_data.get('company') else ''
            position = job_data.get('position', '').strip() if job_data.get('position') else ''
            
            # Validate company
            if not company:
                conn.close()
                self.log("Skipped: No company name", 'SKIP')
                return None
            
            # Check duplicate
            if company and position:
                c.execute("""SELECT job_id FROM jobs 
                            WHERE LOWER(TRIM(company)) = LOWER(?) 
                            AND LOWER(TRIM(position)) = LOWER(?)
                            AND status = 'active'""",
                         (company, position))
                existing = c.fetchone()
            else:
                c.execute("""SELECT job_id FROM jobs 
                            WHERE LOWER(TRIM(company)) = LOWER(?) 
                            AND deadline = ?
                            AND status = 'active'""",
                         (company, job_data.get('deadline')))
                existing = c.fetchone()
            
            if existing:
                conn.close()
                self.log(f"Duplicate skipped: {company} - {position or 'existing'}", 'SKIP')
                return None
            
            # Create new job
            ctc_value = job_data.get('ctc') or job_data.get('stipend') or None
            
            description_parts = []
            if job_data.get('description'):
                description_parts.append(job_data['description'])
            if job_data.get('test_date'):
                description_parts.append(f"Test: {job_data['test_date']}")
            if job_data.get('interview_date'):
                description_parts.append(f"Interview: {job_data['interview_date']}")
            
            full_description = '\n'.join(description_parts) if description_parts else None
            
            # Get job_link from apply_link
            job_link = job_data.get('apply_link')
            
            c.execute('''INSERT INTO jobs 
                        (company, position, ctc, location, job_type, deadline,
                         description, requirements, eligibility, email_date, status, job_link)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (company,
                      position or 'Position',
                      ctc_value,
                      job_data.get('location'),
                      job_data.get('job_type', 'Full-time'),
                      job_data.get('deadline'),
                      full_description,
                      job_data.get('requirements'),
                      job_data.get('eligibility'),
                      email_data.get('date'),
                      'active',
                      job_link))
            
            job_id = c.lastrowid
            conn.commit()
            conn.close()
            
            self.log(f"Job created: ID {job_id} - {company}", 'SUCCESS')
            self.stats['new_jobs'] += 1
            return job_id
                
        except Exception as e:
            self.log(f"Database error: {str(e)[:100]}", 'ERROR')
            return None
    
    # UPDATED METHOD: Now returns email_id
    def mark_email_processed(self, message_id, subject, from_addr, date, job_id=None, skipped=False, skip_reason=None):
        """Mark email as processed - UPDATED to return email_id"""
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute('''INSERT OR REPLACE INTO processed_emails 
                        (message_id, subject, from_email, email_date, processed_at,
                         job_id, job_created, skipped, skip_reason, user_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                     (message_id, subject, from_addr, date, datetime.now(),
                      job_id, (job_id is not None), skipped, skip_reason, self.user_id))
            
            email_id = c.lastrowid
            conn.commit()
            conn.close()
            
            return email_id
            
        except Exception as e:
            self.log(f"Error marking email processed: {e}", 'ERROR')
            return None
    
    def fetch_emails(self):
        """Fetch emails to process"""
        try:
            self.imap.select('INBOX')
            
            # Get processed emails
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT message_id FROM processed_emails WHERE user_id = ?', (self.user_id,))
            processed_ids = set(row[0] for row in c.fetchall())
            conn.close()
            
            # Fetch recent emails
            days_back = 60
            since_date = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')
            
            status, messages = self.imap.search(None, f'(SINCE {since_date})')
            
            if status != 'OK' or not messages[0]:
                return []
            
            email_ids = messages[0].split()
            self.log(f"Found {len(email_ids)} emails in last {days_back} days")
            
            emails_to_process = []
            
            for email_id in email_ids[-self.batch_size:]:
                try:
                    status, msg_data = self.imap.fetch(email_id, '(RFC822)')
                    
                    if status != 'OK' or not msg_data[0]:
                        continue
                    
                    email_message = email.message_from_bytes(msg_data[0][1])
                    email_data = self.parse_email_basic(email_message)
                    
                    if not email_data or email_data['message_id'] in processed_ids:
                        continue
                    
                    body_preview = email_data['body'][:5000]
                    should_process, reason = self.should_process_email(
                        email_data['from'],
                        email_data['to'],
                        email_data['cc'],
                        email_data['subject'],
                        body_preview
                    )
                    
                    if should_process:
                        email_data['process_reason'] = reason
                        emails_to_process.append(email_data)
                    else:
                        self.mark_email_processed(
                            email_data['message_id'],
                            email_data['subject'],
                            email_data['from'],
                            email_data['date'],
                            skipped=True,
                            skip_reason=reason
                        )
                
                except Exception as e:
                    continue
            
            self.log(f"Selected {len(emails_to_process)} emails to process")
            return emails_to_process
            
        except Exception as e:
            self.log(f"Fetch error: {str(e)}", 'ERROR')
            return []
    
    def process_email(self, email_data):
        """Process single email - handles multiple jobs - UPDATED with attachment tracking"""
        print(f"\n{'='*80}")
        print(f"EMAIL: {email_data['subject'][:70]}")
        print(f"FROM: {email_data['from'][:50]}")
        print(f"DATE: {email_data['date'].strftime('%Y-%m-%d %H:%M')}")
        
        reason = email_data.get('process_reason', 'unknown')
        if 'primary_source' in reason:
            source_type = 'primary'
        elif 'nlp' in reason:
            source_type = 'secondary'
        else:
            source_type = 'other'
        
        print(f"SOURCE: {reason}")
        print(f"{'='*80}")
        
        thread_id = self.get_thread_id(email_data)
        
        if thread_id in self.processed_threads:
            self.log("Thread already processed", 'SKIP')
            self.stats['skipped'] += 1
            return
        
        # Fetch thread
        thread_emails = self.fetch_email_thread(email_data)
        
        # Extract content
        all_content, relevant_attachments, all_urls = self.extract_all_content(thread_emails)
        
        # Extract job info
        jobs_data = self.extract_job_info_with_gpt(all_content, all_urls)
        
        jobs_created = 0
        skip_reason = None
        last_job_id = None
        
        if jobs_data:
            for job_data in jobs_data:
                job_id = self.create_job_in_database(job_data, email_data, relevant_attachments)
                if job_id:
                    jobs_created += 1
                    last_job_id = job_id
                    
                    if source_type == 'primary':
                        if 'primary_jobs' not in self.stats:
                            self.stats['primary_jobs'] = 0
                        self.stats['primary_jobs'] += 1
                    elif source_type == 'secondary':
                        if 'secondary_jobs' not in self.stats:
                            self.stats['secondary_jobs'] = 0
                        self.stats['secondary_jobs'] += 1
            
            if jobs_created > 0:
                self.processed_threads.add(thread_id)
                self.log(f"Created {jobs_created} job(s)", 'SUCCESS')
            else:
                skip_reason = "All jobs were duplicates or invalid"
                self.stats['skipped'] += 1
                self.log("All jobs were duplicates or invalid", 'SKIP')
        else:
            skip_reason = "Not a job posting"
            self.stats['skipped'] += 1
            self.log("Skipped - not a job", 'SKIP')
        
        # Mark processed and get email_id
        email_id = self.mark_email_processed(
            email_data['message_id'],
            email_data['subject'],
            email_data['from'],
            email_data['date'],
            job_id=last_job_id if jobs_created > 0 else None,
            skipped=(jobs_created == 0),
            skip_reason=skip_reason
        )
        
        # NEW: Save attachments to database if email was processed and has attachments
        if email_id and relevant_attachments:
            self.log(f"Saving {len(relevant_attachments)} attachment(s) to database")
            for attachment in relevant_attachments:
                self.save_attachment_to_db(
                    email_id,
                    attachment['filename'],
                    attachment['path']
                )
        
        self.stats['processed'] += 1
        
        if source_type == 'primary':
            if 'primary_processed' not in self.stats:
                self.stats['primary_processed'] = 0
            self.stats['primary_processed'] += 1
        elif source_type == 'secondary':
            if 'secondary_processed' not in self.stats:
                self.stats['secondary_processed'] = 0
            self.stats['secondary_processed'] += 1
        
        print(f"RESULT: {'✅ ' + str(jobs_created) + ' JOB(S) CREATED' if jobs_created > 0 else '❌ SKIPPED'}")
        print(f"{'='*80}\n")
    
    def run(self, continuous=False):
        """Main execution with optional continuous monitoring"""
        print("\n" + "="*80)
        print("EMAIL JOB PROCESSOR - STARTING")
        if continuous:
            print("MODE: CONTINUOUS MONITORING (checking every 5 seconds)")
        else:
            print("MODE: ONE-TIME BATCH PROCESSING ",self.batch_size)
        print("="*80)
        
        try:
            if not self.load_credentials():
                raise Exception("Failed to load credentials")
            
            if not self.connect_to_email():
                raise Exception("Failed to connect")
            
            # First run - process batch
            self.log("Initial batch processing...")
            emails_data = self.fetch_emails()
            
            if emails_data:
                for idx, email_data in enumerate(emails_data, 1):
                    print(f"\n>>> Processing email {idx}/{len(emails_data)}")
                    try:
                        self.process_email(email_data)
                    except Exception as e:
                        self.log(f"Error: {str(e)[:100]}", 'ERROR')
                        self.mark_email_processed(
                            email_data['message_id'],
                            email_data['subject'],
                            email_data['from'],
                            email_data['date'],
                            skipped=True,
                            skip_reason=f"Error: {str(e)[:50]}"
                        )
                        self.stats['skipped'] += 1
            else:
                self.log("No emails to process in initial batch")
            
            # Print summary
            self.print_summary()
            
            # If continuous mode, start monitoring
            if continuous:
                self.continuous_monitor()
            
        except KeyboardInterrupt:
            print("\n\n🛑 Stopped by user (Ctrl+C)")
            self.print_summary()
        except Exception as e:
            self.log(f"Fatal error: {str(e)}", 'ERROR')
        finally:
            if self.imap:
                try:
                    self.imap.close()
                    self.imap.logout()
                except:
                    pass
    
    def continuous_monitor(self):
        """Continuously monitor for new emails every 5 seconds"""
        import time
        
        print("\n" + "="*80)
        print("CONTINUOUS MONITORING STARTED")
        print("Checking for new emails every 5 seconds...")
        print("Press Ctrl+C to stop")
        print("="*80 + "\n")
        
        last_check = datetime.now()
        check_count = 0
        
        try:
            while True:
                check_count += 1
                current_time = datetime.now()
                
                # Reconnect if needed (every 100 checks ~8 minutes)
                if check_count % 100 == 0:
                    try:
                        self.imap.select('INBOX')
                    except:
                        self.log("Reconnecting to email server...", 'INFO')
                        if not self.connect_to_email():
                            self.log("Reconnection failed. Exiting.", 'ERROR')
                            break
                
                # Fetch new emails
                try:
                    new_emails = self.fetch_new_emails_since(last_check)
                    
                    if new_emails:
                        print(f"\n⚡ [{current_time.strftime('%H:%M:%S')}] Found {len(new_emails)} new email(s)")
                        
                        for idx, email_data in enumerate(new_emails, 1):
                            print(f"\n>>> Processing new email {idx}/{len(new_emails)}")
                            try:
                                self.process_email(email_data)
                            except Exception as e:
                                self.log(f"Error: {str(e)[:100]}", 'ERROR')
                                self.mark_email_processed(
                                    email_data['message_id'],
                                    email_data['subject'],
                                    email_data['from'],
                                    email_data['date'],
                                    skipped=True,
                                    skip_reason=f"Error: {str(e)[:50]}"
                                )
                                self.stats['skipped'] += 1
                        
                        last_check = current_time
                    else:
                        # Print progress dot every 10 checks (50 seconds)
                        if check_count % 10 == 0:
                            print(f"⏱ [{current_time.strftime('%H:%M:%S')}] No new emails (checked {check_count} times)", end='\r')
                
                except Exception as e:
                    self.log(f"Monitor error: {str(e)[:100]}", 'ERROR')
                
                # Wait 5 seconds
                time.sleep(5)
                
        except KeyboardInterrupt:
            print("\n\n🛑 Monitoring stopped by user")

    def fetch_new_emails_since(self, since_time):
        """
        Fetch only new emails received after a specific time
        Enhanced version for live monitoring with detailed logs
        """
        print("\n[FETCH] Starting fetch_new_emails_since()")
        print(f"[FETCH] Checking for new emails since: {since_time}")

        try:
            self.imap.select('INBOX')
            print("[FETCH] IMAP inbox selected successfully.")

            # Get already processed emails from database
            print("[DB] Loading processed email message IDs from database...")
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT message_id FROM processed_emails WHERE user_id = ?', (self.user_id,))
            processed_ids = set(row[0] for row in c.fetchall())
            conn.close()
            print(f"[DB] Retrieved {len(processed_ids)} processed message IDs.")

            # Search for recent emails (last 1 hour window)
            since_date = (datetime.now() - timedelta(hours=1)).strftime('%d-%b-%Y')
            print(f"[FETCH] Searching IMAP for emails since {since_date}...")

            status, messages = self.imap.search(None, f'(SINCE {since_date})')

            if status != 'OK' or not messages[0]:
                print("[FETCH] No new emails found or IMAP search failed.")
                return []

            email_ids = messages[0].split()
            print(f"[FETCH] Found {len(email_ids)} emails matching criteria.")
            emails_to_process = []

            # Make since_time timezone-naive for comparison
            if since_time.tzinfo is not None:
                since_time_naive = since_time.replace(tzinfo=None)
            else:
                since_time_naive = since_time

            # Process only newest emails (last 20)
            for idx, email_id in enumerate(email_ids[-20:], start=1):
                print(f"[EMAIL-{idx}] Fetching email ID: {email_id.decode('utf-8', 'ignore')}")
                try:
                    status, msg_data = self.imap.fetch(email_id, '(RFC822)')
                    if status != 'OK' or not msg_data[0]:
                        print(f"[EMAIL-{idx}] Skipping — invalid fetch response.")
                        continue

                    email_message = email.message_from_bytes(msg_data[0][1])
                    email_data = self.parse_email_basic(email_message)

                    if not email_data:
                        print(f"[EMAIL-{idx}] Skipping — could not parse email content.")
                        continue

                    msg_id = email_data['message_id']
                    print(f"[EMAIL-{idx}] Parsed email — Subject: {email_data['subject']}, From: {email_data['from']}")

                    # Skip if already processed
                    if msg_id in processed_ids:
                        print(f"[EMAIL-{idx}] Skipping — already processed (msg_id={msg_id}).")
                        continue

                    # Convert email_data['date'] to timezone-naive for comparison
                    email_date = email_data['date']
                    if email_date.tzinfo is not None:
                        email_date_naive = email_date.replace(tzinfo=None)
                    else:
                        email_date_naive = email_date

                    # Skip if older than since_time
                    if email_date_naive < since_time_naive:
                        print(f"[EMAIL-{idx}] Skipping — older than last check time ({email_date}).")
                        continue

                    # Check if should process
                    body_preview = email_data['body'][:500]
                    should_process, reason = self.should_process_email(
                        email_data['from'],
                        email_data['to'],
                        email_data['cc'],
                        email_data['subject'],
                        body_preview
                    )

                    if should_process:
                        email_data['process_reason'] = reason
                        emails_to_process.append(email_data)
                        print(f"[EMAIL-{idx}] ✅ Marked for processing — Reason: {reason}")
                    else:
                        print(f"[EMAIL-{idx}] ❌ Skipped — Reason: {reason}")
                        self.mark_email_processed(
                            email_data['message_id'],
                            email_data['subject'],
                            email_data['from'],
                            email_data['date'],
                            skipped=True,
                            skip_reason=reason
                        )

                except Exception as e:
                    print(f"[EMAIL-{idx}] ⚠️ Error processing this email: {e}")
                    continue

            print(f"[FETCH] Completed — {len(emails_to_process)} emails ready for processing.\n")
            return emails_to_process

        except Exception as e:
            print(f"[FETCH-ERROR] Error while fetching new emails: {e}")
            return []
    
    def print_summary(self):
        """Print processing summary"""
        print("\n" + "="*80)
        print("PROCESSING SUMMARY")
        print("="*80)
        print(f"Total emails processed: {self.stats['processed']}")
        print(f"New jobs created: {self.stats['new_jobs']}")
        print(f"Emails skipped: {self.stats['skipped']}")
        print("-" * 80)
        
        primary_processed = self.stats.get('primary_processed', 0)
        secondary_processed = self.stats.get('secondary_processed', 0)
        primary_jobs = self.stats.get('primary_jobs', 0)
        secondary_jobs = self.stats.get('secondary_jobs', 0)
        
        print("BY SOURCE:")
        print(f"  Primary (spr@thapar.edu/CampusNotice2026):")
        print(f"    - Emails processed: {primary_processed}")
        print(f"    - Jobs created: {primary_jobs}")
        
        print(f"  Secondary (NLP-based / Other sources):")
        print(f"    - Emails processed: {secondary_processed}")
        print(f"    - Jobs created: {secondary_jobs}")
        
        print("-" * 80)
        
        if self.stats['processed'] > 0:
            success_rate = (self.stats['new_jobs'] / self.stats['processed']) * 100
            print(f"Success rate: {success_rate:.1f}%")
        
        print("="*80 + "\n")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Email Job Processor')
    parser.add_argument('--batch-size', type=int, default=50, help='Number of emails to process in initial batch')
    parser.add_argument('--user-id', type=int, help='User ID')
    parser.add_argument('--continuous', action='store_true', help='Enable continuous monitoring mode (checks every 5 seconds)')
    
    args = parser.parse_args()
    
    processor = EmailProcessor(user_id=args.user_id, batch_size=args.batch_size)
    processor.run(continuous=args.continuous)


if __name__ == '__main__':
    main()