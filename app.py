import http.server
import socketserver
import json
import sqlite3
import urllib.parse
import os

try:
    import psycopg2
    import psycopg2.extras
    HAS_PG = True
except ImportError:
    HAS_PG = False

PORT = int(os.environ.get('PORT', 8888))
DB_URL = os.environ.get('DATABASE_URL')
DB_NAME = 'words.db'

def get_db():
    if DB_URL and HAS_PG:
        conn = psycopg2.connect(DB_URL)
        return conn, 'pg'
    else:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'

def init_db():
    conn, db_type = get_db()
    cursor = conn.cursor()
    if db_type == 'pg':
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL UNIQUE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS words (
                id SERIAL PRIMARY KEY,
                word TEXT NOT NULL,
                translation TEXT NOT NULL,
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("INSERT INTO categories (name) VALUES ('預設') ON CONFLICT (name) DO NOTHING")
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL,
                translation TEXT NOT NULL,
                category_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
            )
        ''')
        cursor.execute("INSERT OR IGNORE INTO categories (name) VALUES ('預設')")
    
    conn.commit()
    conn.close()

class CustomHandler(http.server.BaseHTTPRequestHandler):
    def send_html_file(self, filename):
        html_path = os.path.join(os.path.dirname(__file__), filename)
        if os.path.exists(html_path):
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            with open(html_path, 'rb') as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, f"找不到 {filename} 檔案！")

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        
        if parsed_path.path == '/' or parsed_path.path == '/index.html':
            self.send_html_file('index.html')
            return

        if parsed_path.path == '/admin' or parsed_path.path == '/admin.html':
            self.send_html_file('admin.html')
            return

        if parsed_path.path == '/api/words':
            query = urllib.parse.parse_qs(parsed_path.query)
            cat_name = query.get('category', ['ALL'])[0]
            conn, db_type = get_db()
            
            if db_type == 'pg':
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            else:
                cursor = conn.cursor()

            if cat_name != 'ALL':
                sql = '''SELECT w.id, w.word, w.translation, c.name AS category FROM words w LEFT JOIN categories c ON w.category_id = c.id WHERE c.name = ? ORDER BY w.id DESC'''
                if db_type == 'pg': sql = sql.replace('?', '%s')
                cursor.execute(sql, (cat_name,))
            else:
                sql = '''SELECT w.id, w.word, w.translation, COALESCE(c.name, '未分類') AS category FROM words w LEFT JOIN categories c ON w.category_id = c.id ORDER BY w.id DESC'''
                cursor.execute(sql)
            
            rows = cursor.fetchall()
            conn.close()
            
            words = [dict(r) for r in rows]
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(words).encode('utf-8'))
            return

        if parsed_path.path == '/api/categories':
            conn, db_type = get_db()
            if db_type == 'pg':
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            else:
                cursor = conn.cursor()
                
            cursor.execute('SELECT name FROM categories ORDER BY id ASC')
            rows = cursor.fetchall()
            conn.close()
            
            cats = [r['name'] for r in rows]
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(cats).encode('utf-8'))
            return

        self.send_error(404)

    def do_POST(self):
        if self.path == '/api/words/batch':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            text_content = data.get('text_content', '')
            cat_name = data.get('category', '預設').strip() or '預設'

            conn, db_type = get_db()
            cursor = conn.cursor()

            if db_type == 'pg':
                cursor.execute("INSERT INTO categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (cat_name,))
                cursor.execute("SELECT id FROM categories WHERE name = %s", (cat_name,))
                cat_id = cursor.fetchone()[0]
            else:
                cursor.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat_name,))
                cursor.execute("SELECT id FROM categories WHERE name = ?", (cat_name,))
                cat_id = cursor.fetchone()['id']

            imported_count = 0
            lines = text_content.splitlines()
            for line in lines:
                line = line.strip()
                if not line: continue
                
                if ',' in line or '\t' in line:
                    parts = line.replace('\t', ',').split(',', 1)
                    word = parts[0].strip()
                    trans = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "(未填寫)"
                else:
                    word = line.strip()
                    trans = "(未填寫)"

                if word:
                    sql = "INSERT INTO words (word, translation, category_id) VALUES (?, ?, ?)"
                    if db_type == 'pg': sql = sql.replace('?', '%s')
                    cursor.execute(sql, (word, trans, cat_id))
                    imported_count += 1

            conn.commit()
            conn.close()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'message': 'Success', 'count': imported_count}).encode('utf-8'))
            return

        if self.path == '/api/words':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            word = data.get('word', '').strip()
            translation = data.get('translation', '').strip() or "(未填寫)"
            cat_name = data.get('category', '預設').strip() or '預設'

            conn, db_type = get_db()
            cursor = conn.cursor()

            if db_type == 'pg':
                cursor.execute("INSERT INTO categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (cat_name,))
                cursor.execute("SELECT id FROM categories WHERE name = %s", (cat_name,))
                cat_id = cursor.fetchone()[0]
                cursor.execute("INSERT INTO words (word, translation, category_id) VALUES (%s, %s, %s)", (word, translation, cat_id))
            else:
                cursor.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat_name,))
                cursor.execute("SELECT id FROM categories WHERE name = ?", (cat_name,))
                cat_id = cursor.fetchone()['id']
                cursor.execute("INSERT INTO words (word, translation, category_id) VALUES (?, ?, ?)", (word, translation, cat_id))

            conn.commit()
            conn.close()

            self.send_response(201)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'message': 'Success'}).encode('utf-8'))
            return

    def do_DELETE(self):
        if self.path.startswith('/api/words/'):
            word_id = int(self.path.split('/')[-1])
            conn, db_type = get_db()
            cursor = conn.cursor()
            
            sql = 'DELETE FROM words WHERE id = ?'
            if db_type == 'pg': sql = sql.replace('?', '%s')
            
            cursor.execute(sql, (word_id,))
            conn.commit()
            conn.close()
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode('utf-8'))
            return

if __name__ == '__main__':
    init_db()
    socketserver.TCPServer.allow_reuse_address = True
    print(f"\n==========================================")
    print(f"🚀 伺服器啟動！PORT: {PORT}")
    print(f"==========================================\n")
    with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
        httpd.serve_forever()
