import os
import threading
import psycopg2
from psycopg2 import pool
import barcode
from barcode.writer import ImageWriter
from flask import Flask, request, jsonify
from supabase import create_client

app = Flask(__name__)

# Supabase Database Connection Pool
DB_CONFIG = {
    "dbname": "postgres",
    "user": "postgres.kpwsabrvzergvzpgilhy",
    "password": "wMzRwtVTHNGMa4VS",
    "host": "aws-0-ap-southeast-1.pooler.supabase.com",
    "port": "6543"
}
db_pool = pool.SimpleConnectionPool(1, 10, **DB_CONFIG)

# Supabase Configuration
SUPABASE_URL = "https://kpwsabrvzergvzpgilhy.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtwd3NhYnJ2emVyZ3Z6cGdpbGh5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Mzk5NTc0OTIsImV4cCI6MjA1NTUzMzQ5Mn0.9BH4btGMf3GzS_1gw2DXyzlAlBlnRGARJCQC1blV5W0"
SUPABASE_BUCKET = "barcode_images"

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

def upload_to_supabase(image_path, gtin):
    """Uploads barcode image to Supabase Storage and returns the public URL."""
    try:
        with open(image_path, "rb") as f:
            response = supabase.storage.from_(SUPABASE_BUCKET).upload(
                f"static/{gtin}.png", f, {"content-type": "image/png"}
            )

        # Get the public URL
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/static/{gtin}.png"
        return public_url
    except Exception as e:
        print(f"Error uploading to Supabase: {e}")
        return None


def generate_gs1_barcode(gtin):
    """Generates GS1 barcode, uploads it to Supabase, and returns the URL."""
    try:
        ean = barcode.get_barcode_class('ean13')
        barcode_instance = ean(gtin, writer=ImageWriter())

        # Save barcode temporarily
        temp_path = f"/tmp/{gtin}.png"
        barcode_instance.save(temp_path)

        # Upload to Supabase
        public_url = upload_to_supabase(temp_path, gtin)

        # Delete temporary file
        os.remove(temp_path)

        return public_url
    except Exception as e:
        print(f"Error generating barcode: {e}")
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

@app.route('/generate_barcode', methods=['POST'])
def generate_barcode():
    """API endpoint to generate a barcode and store product details."""
    data = request.json
    name = data.get("name")
    price = data.get("price")
    gtin_input = data.get("gtin")

    if not name or not price:
        return jsonify({"error": "Missing required fields"}), 400

    if gtin_input:
        try:
            gtin = calculate_gtin13(gtin_input[:12])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    else:
        return jsonify({"error": "GTIN required"}), 400

    barcode_url = generate_gs1_barcode(gtin)

    if not barcode_url:
        return jsonify({"error": "Failed to generate barcode"}), 500

    if not store_product_in_db(name, price, gtin, barcode_url):
        return jsonify({"error": "Database error"}), 500

    return jsonify({
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
        return jsonify({"error": "GTIN is required"}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT name, price, barcode_image_path FROM products WHERE gtin = %s", (gtin,))
        product = cur.fetchone()
        cur.close()
        release_db_connection(conn)

        if not product:
            return jsonify({"error": "Product not found"}), 404

        return jsonify({
            "name": product[0],
            "price": product[1],
            "barcode_image_path": product[2]  # This is now a Supabase URL
        }), 200

    except Exception as e:
        print(f"Database Error: {e}")
        return jsonify({"error": "Database error"}), 500


if __name__ == '__main__':
    app.run(port=5001, threaded=True)
