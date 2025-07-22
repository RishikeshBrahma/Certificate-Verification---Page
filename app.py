from flask import Flask, request, render_template, redirect, url_for, send_file
import mysql.connector
import pandas as pd
import qrcode
import os
import socket
from dotenv import load_dotenv
import zipfile
import io

app = Flask(__name__)

# Load environment variables
load_dotenv()

# Updated database configuration to include the port
db_config = {
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
    'host': os.getenv('MYSQL_HOST'),
    'database': os.getenv('MYSQL_DB'),
    'port': os.getenv('MYSQL_PORT') # This line is added for Aiven
}

# Define folder paths
# Note: On hosting platforms like Render, the filesystem is ephemeral.
# This means files saved to these folders will be deleted on the next deploy.
# This is acceptable for this app's logic, as files are processed immediately.
UPLOAD_FOLDER = 'uploads'
QR_FOLDER = 'static/qrcodes'

# Ensure the upload and QR code directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# This function is not used in the deployed version but is kept for completeness.
def get_local_ip():
    """Get the local IP address of the machine"""
    try:
        # Connect to a remote server to get the local IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['GET', 'POST'])
def upload_csv():
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            return render_template('upload.html', error='No file uploaded.')

        file = request.files['csv_file']
        filename = file.filename

        if not filename:
            return render_template('upload.html', error='No file selected.')

        if not filename.endswith('.csv'):
            return render_template('upload.html', error='Invalid file format. Only .csv files allowed.')

        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        try:
            df = pd.read_csv(file_path)
            required_columns = ['certificate_id', 'recipient_name', 'course_title', 'issue_date']

            missing_cols = [col for col in required_columns if col not in df.columns]
            if missing_cols:
                raise ValueError(f"Missing required columns: {', '.join(missing_cols)}")

            conn = mysql.connector.connect(**db_config)
            cursor = conn.cursor()

            for _, row in df.iterrows():
                certificate_id = str(row['certificate_id'])
                recipient_name = str(row['recipient_name'])
                course_title = str(row['course_title'])
                issue_date = str(row['issue_date'])

                verification_url = f'{request.host_url}verify/{certificate_id}'
                qr = qrcode.QRCode(version=1, box_size=10, border=4)
                qr.add_data(verification_url)
                qr_image = qr.make_image(fill_color="black", back_color="white")
                qr_path = os.path.join(QR_FOLDER, f'{certificate_id}.png')
                with open(qr_path, 'wb') as qr_file:
                    qr_image.save(qr_file)

                # Use ON DUPLICATE KEY UPDATE to prevent errors on re-upload
                query = """
                    INSERT INTO certificates 
                    (certificate_id, recipient_name, course_title, issue_date, verification_url, csv_file_path)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                    recipient_name=%s, course_title=%s, issue_date=%s, verification_url=%s, csv_file_path=%s
                """
                values = (certificate_id, recipient_name, course_title, issue_date, verification_url, file_path,
                          recipient_name, course_title, issue_date, verification_url, file_path)
                cursor.execute(query, values)

            conn.commit()
            cursor.close()
            conn.close()

            return render_template('upload.html', success='Certificates processed successfully!', filename=filename)

        except Exception as e:
            return render_template('upload.html', error=f'Error processing CSV: {str(e)}')

    return render_template('upload.html')

@app.route('/verify', methods=['GET'])
@app.route('/verify/<certificate_id>', methods=['GET'])
def verify(certificate_id=None):
    if not certificate_id:
        certificate_id = request.args.get('certificate_id')

    if not certificate_id:
        return render_template('verify.html')

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM certificates WHERE certificate_id = %s", (certificate_id,))
        certificate = cursor.fetchone()
        cursor.close()
        conn.close()

        if not certificate:
            return render_template('verify.html', error='Certificate not found.', certificate_id=certificate_id)

        return render_template('verify.html', certificate=certificate, certificate_id=certificate_id)

    except Exception as e:
        return render_template('verify.html', error=f'Database error: {str(e)}', certificate_id=certificate_id)

@app.route('/verify_download/<certificate_id>', methods=['GET'])
def verify_download(certificate_id):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        # Fetch the full certificate data
        cursor.execute("SELECT certificate_id, recipient_name, course_title, issue_date FROM certificates WHERE certificate_id = %s", (certificate_id,))
        certificate = cursor.fetchone()
        cursor.close()
        conn.close()

        if not certificate:
            return render_template('verify.html', error='Certificate not found.', certificate_id=certificate_id)

        # Create a new CSV in memory
        output = io.StringIO()
        df = pd.DataFrame([certificate])
        df.to_csv(output, index=False)
        output.seek(0)

        # Send the in-memory file
        return send_file(
            io.BytesIO(output.getvalue().encode()),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'certificate_{certificate_id}.csv'
        )

    except Exception as e:
        return render_template('verify.html', error=f'Error retrieving file: {str(e)}', certificate_id=certificate_id)

@app.route('/bulk_download_qrcodes/<filename>')
def bulk_download_qrcodes(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    if not os.path.exists(file_path):
        return "File not found.", 404

    df = pd.read_csv(file_path)
    memory_file = io.BytesIO()

    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for _, row in df.iterrows():
            certificate_id = str(row['certificate_id'])
            qr_path = os.path.join(QR_FOLDER, f'{certificate_id}.png')
            if os.path.exists(qr_path):
                zf.write(qr_path, arcname=f'{certificate_id}.png')

    memory_file.seek(0)
    return send_file(memory_file, download_name='qrcodes.zip', as_attachment=True)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
# This is the main entry point for the Flask application.