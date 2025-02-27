import os
import threading
import psycopg2
from psycopg2 import pool
import barcode
from barcode.writer import ImageWriter
from flask import Flask, request, jsonify
from supabase import create_client
from dotenv import load_dotenv
import qrcode
import qrcode

load_dotenv()  # Load environment variables from .env file

app = Flask(__name__)

# Supabase Database Connection Pool
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "sslmode": "require"  # Enforce SSL connection
}

db_pool = pool.SimpleConnectionPool(1, 10, **DB_CONFIG)

# Supabase Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET")
QR_SUPABASE_BUCKET = os.getenv("QR_CODE_BUCKET")
QR_SUPABASE_BUCKET = os.getenv("QR_CODE_BUCKET")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_db_connection():
    return db_pool.getconn()

def release_db_connection(conn):
    db_pool.putconn(conn)

def calculate_gtin13(gtin12):
    """Calculates the GTIN-13 check digit."""
    if len(gtin12) != 12 or not gtin12.isdigit():
        raise ValueError("GTIN-12 must be exactly 12 digits long")

    odd_sum = sum(int(gtin12[i]) for i in range(0, 12, 2))
    even_sum = sum(int(gtin12[i]) for i in range(1, 12, 2)) * 3
    check_digit = (10 - ((odd_sum + even_sum) % 10)) % 10
    return gtin12 + str(check_digit)

def generate_gs1_barcode(gtin):
    """Generates GS1 barcode and saves it to the /tmp directory."""
    try:
        barcode_path = f"/tmp/{gtin}"
        ean = barcode.get_barcode_class('ean13')
        barcode_instance = ean(gtin, writer=ImageWriter())

        full_path = barcode_instance.save(barcode_path)

        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Barcode image not created at {full_path}")

        return full_path
    except Exception as e:
        print(f"Error generating barcode: {e}")
        return None

def upload_to_supabase(image_path, gtin):
    """Uploads barcode image to Supabase Storage and returns the public URL."""
    try:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"File not found: {image_path}")

        with open(image_path, "rb") as f:
            response = supabase.storage.from_(SUPABASE_BUCKET).upload(
                f"static/{gtin}.png", f, {"content-type": "image/png"}
            )

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/static/{gtin}.png"
        return public_url
    except Exception as e:
        print(f"Error uploading to Supabase: {e}")
        return None

def store_product_in_db(name, price, gtin, barcode_url):
    """Stores product details in the database."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO products (name, price, gtin, barcode_image_path) VALUES (%s, %s, %s, %s)",
            (name, price, gtin, barcode_url)
        )
        conn.commit()
        cur.close()
        release_db_connection(conn)
    except Exception as e:
        print(f"Database Error: {e}")
        return False
    return True

def check_if_barcode_exists(gtin):
    """Checks if a barcode (GTIN) already exists in the database."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM products WHERE gtin = %s", (gtin,))
        exists = cur.fetchone()
        cur.close()
        release_db_connection(conn)
        return exists is not None
    except Exception as e:
        print(f"Database Error: {e}")
        return False

def check_if_qr_exists(name):
    """Checks if a QR code already exists for a given product name."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM qr_codes WHERE name = %s LIMIT 1", (name,))
        exists = cur.fetchone()
        cur.close()
        release_db_connection(conn)
        return exists is not None
    except Exception as e:
        print(f"Database Error: {e}")
        return False

def generate_qr_code(name, price):
    """Generates a QR code and saves it to the /tmp directory."""
    try:
        qr_data = f"Product: {name}, Price: {price}"
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_data)
        qr.make(fit=True)

        qr_path = f"/tmp/{name}_qr.png"
        img = qr.make_image(fill="black", back_color="white")
        img.save(qr_path)

        return qr_path
    except Exception as e:
        print(f"Error generating QR Code: {e}")
        return None

def upload_qr_to_supabase(image_path, name):
    """Uploads QR code image to the 'qr_codes' bucket and returns the public URL."""
    try:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"File not found: {image_path}")

        with open(image_path, "rb") as f:
            response = supabase.storage.from_(QR_SUPABASE_BUCKET).upload(
                f"static/{name}_qr.png", f, {"content-type": "image/png"}
            )

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{QR_SUPABASE_BUCKET}/static/{name}_qr.png"
        return public_url
    except Exception as e:
        print(f"Error uploading QR code to Supabase: {e}")
        return None

def store_qr_in_db(name, price, qr_url):
    """Stores QR code details in the qr_codes table."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO qr_codes (name, price, qr_code_image_path) VALUES (%s, %s, %s)",
            (name, price, qr_url)
        )
        conn.commit()
        cur.close()
        release_db_connection(conn)
    except Exception as e:
        print(f"Database Error: {e}")
        return False
    return True

@app.route('/generate_qrcode', methods=['POST'])
def generate_qrcode():
    """API endpoint to generate a QR code and store product details separately."""
    data = request.json
    name = data.get("name")
    price = data.get("price")

    if not name or not price:
        return jsonify({"isSuccess": False, "message": "Missing required fields"}), 400

    if check_if_qr_exists(name):
        return jsonify({"isSuccess": False, "message": "QR Code already exists"}), 400

    # Generate QR Code
    qr_path = generate_qr_code(name, price)
    if not qr_path:
        return jsonify({"isSuccess": False, "message": "Failed to generate QR Code"}), 500

    # Upload to Supabase
    qr_url = upload_qr_to_supabase(qr_path, name)
    if not qr_url:
        return jsonify({"isSuccess": False, "message": "Failed to upload QR Code"}), 500

    # Store in Database
    if not store_qr_in_db(name, price, qr_url):
        return jsonify({"isSuccess": False, "message": "Database error"}), 500

    return jsonify({
        "isSuccess": True,
        "message": "QR Code generated and stored successfully",
        "name": name,
        "qr_code_image_path": qr_url
    }), 201


@app.route('/get_qr', methods=['POST'])
def get_qr():
    """Fetches QR code details from the database using name passed in request body."""
    data = request.json
    name = data.get("name")

    if not name:
        return jsonify({"isSuccess": False, "message": "Name is required"}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT name, price, qr_code_image_path FROM qr_codes WHERE name = %s", (name,))
        qr_data = cur.fetchone()
        cur.close()
        release_db_connection(conn)

        if qr_data:
            return jsonify({
                "isSuccess": True,
                "message": "QR Code found successfully",
                "name": qr_data[0],
                "price": float(qr_data[1]),
                "qr_code_image_path": qr_data[2]
            }), 200
        else:
            return jsonify({"isSuccess": False, "message": "QR Code not found"}), 404
    except Exception as e:
        print(f"Database Error: {e}")
        return jsonify({"isSuccess": False, "message": "Internal server error"}), 500


@app.route('/generate_barcode', methods=['POST'])
def generate_barcode():
    """API endpoint to generate a barcode and store product details."""
    data = request.json
    name = data.get("name")
    price = data.get("price")
    gtin_input = data.get("gtin")

    if not name or not price:
        return jsonify({"isSuccess": False, "message": "Missing required fields"}), 400

    if gtin_input:
        try:
            gtin = calculate_gtin13(gtin_input[:12])
        except ValueError as e:
            return jsonify({"isSuccess": False, "message": str(e)}), 400
    else:
        return jsonify({"isSuccess": False, "message": "GTIN required"}), 400

    if check_if_barcode_exists(gtin):
        return jsonify({"isSuccess": False, "message": "Barcode already exists"}), 400

    barcode_path = generate_gs1_barcode(gtin)
    if not barcode_path:
        return jsonify({"isSuccess": False, "message": "Failed to generate barcode"}), 500

    barcode_url = upload_to_supabase(barcode_path, gtin)
    if not barcode_url:
        return jsonify({"isSuccess": False, "message": "Failed to upload barcode"}), 500

    if not store_product_in_db(name, price, gtin, barcode_url):
        return jsonify({"isSuccess": False, "message": "Database error"}), 500

    return jsonify({
        "isSuccess": True,
        "message": "Barcode generated and product stored successfully",
        "gtin": gtin,
        "barcode_image_path": barcode_url
    }), 201

@app.route('/scan_barcode', methods=['POST'])
def scan_barcode():
    """API endpoint to scan a barcode and retrieve product details."""
    data = request.json
    gtin = data.get("gtin")

    if not gtin:
        return jsonify({"isSuccess": False, "message": "GTIN is required"}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT name, price, barcode_image_path FROM products WHERE gtin = %s", (gtin,))
        product = cur.fetchone()
        cur.close()
        release_db_connection(conn)

        if not product:
            return jsonify({"isSuccess": False, "message": "Product not found"}), 404

        return jsonify({
            "isSuccess": True,
            "message": "Product found successfully",
            "name": product[0],
            "price": product[1],
            "barcode_image_path": product[2]
        }), 200

    except Exception as e:
        print(f"Database Error: {e}")
        return jsonify({"isSuccess": False, "message": "Database error"}), 500

if __name__ == '__main__':
    app.run(port=5001, threaded=True)