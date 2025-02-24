import os
import threading
import psycopg2
from psycopg2 import pool
import barcode
from barcode.writer import ImageWriter
from flask import Flask, request, jsonify

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

# Ensure 'static' directory exists
os.makedirs("static", exist_ok=True)

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
    """Generates GS1 barcode asynchronously."""
    def worker():
        try:
            ean = barcode.get_barcode_class('ean13')
            barcode_instance = ean(gtin, writer=ImageWriter())
            barcode_instance.save(f"static/{gtin}.png")
        except Exception as e:
            print(f"Error generating barcode: {e}")

    thread = threading.Thread(target=worker)
    thread.start()
    return f"static/{gtin}.png"

def store_product_in_db(name, price, gtin, barcode_image_path):
    """Stores product details in the database."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO products (name, price, gtin, barcode_image_path) VALUES (%s, %s, %s, %s)",
            (name, price, gtin, barcode_image_path)
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

    barcode_image_path = generate_gs1_barcode(gtin)

    if not store_product_in_db(name, price, gtin, barcode_image_path):
        return jsonify({"error": "Database error"}), 500

    return jsonify({
        "message": "Barcode generated and product stored successfully",
        "gtin": gtin,
        "barcode_image_path": barcode_image_path
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
            "barcode_image_path": product[2]
        }), 200

    except Exception as e:
        print(f"Database Error: {e}")
        return jsonify({"error": "Database error"}), 500



if __name__ == '__main__':
    app.run(port=5001, threaded=True)
