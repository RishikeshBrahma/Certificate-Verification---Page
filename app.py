from flask import Flask, request, render_template, redirect, url_for, send_file
import mysql.connector
import pandas as pd
import qrcode
import os
from dotenv import load_dotenv

app = Flask(__name__)

# Load environment variables
load_dotenv()

db_config = {
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
    'host': os.getenv('MYSQL_HOST'),
    'database': os.getenv('MYSQL_DB')
}

UPLOAD_FOLDER = 'uploads'
QR_FOLDER = 'static/qrcodes'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(QR_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

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

                verification_url = f'{request.host_url}verify_download/{certificate_id}'
                qr = qrcode.QRCode(version=1, box_size=10, border=4)
                qr.add_data(verification_url)
                qr_image = qr.make_image(fill_color="black", back_color="white")
                qr_path = os.path.join(QR_FOLDER, f'{certificate_id}.png')
                with open(qr_path, 'wb') as qr_file:
                    qr_image.save(qr_file)

                query = """
                    INSERT INTO certificates 
                    (certificate_id, recipient_name, course_title, issue_date, verification_url, csv_file_path)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """
                values = (certificate_id, recipient_name, course_title, issue_date, verification_url, file_path)
                cursor.execute(query, values)

            conn.commit()
            cursor.close()
            conn.close()

            return render_template('upload.html', success='Certificates processed successfully!')

        except Exception as e:
            return render_template('upload.html', error=f'Error processing CSV: {str(e)}')

    return render_template('upload.html')

@app.route('/verify', methods=['GET'])
def verify():
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

        return render_template('verify.html', certificate_id=certificate_id)

    except Exception as e:
        return render_template('verify.html', error=f'Database error: {str(e)}', certificate_id=certificate_id)

@app.route('/verify_download/<certificate_id>', methods=['GET'])
def verify_download(certificate_id):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT csv_file_path FROM certificates WHERE certificate_id = %s", (certificate_id,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if not result or 'csv_file_path' not in result:
            return render_template('verify.html', error='CSV file not found.', certificate_id=certificate_id)

        # Explicitly cast to string
        csv_path = str(result['csv_file_path']) # type: ignore

        if not os.path.exists(csv_path):
            return render_template('verify.html', error='File does not exist on server.', certificate_id=certificate_id)

        return send_file(csv_path, as_attachment=True, download_name=f'certificate_{certificate_id}.csv')

    except Exception as e:
        return render_template('verify.html', error=f'Error retrieving file: {str(e)}', certificate_id=certificate_id)
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)