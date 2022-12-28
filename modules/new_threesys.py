
import fitz
from pylibdmtx.pylibdmtx import decode as pylibdmtx_decode
import os
import psycopg2
from psycopg2 import Error
import json
import treepoem
import datetime
import io
import math


ALLOWED_EXTENSIONS = {"pdf"}
url = os.getenv("DATABASE_URL")
allowance = 3


# checks the request file if it is a pdf. If it is, then it is read into
# memory for api manipulation
def initialize_request(req):
    file = req.files["file"]
    if (
        "file" not in req.files
        or req.files["file"] == ""
        or not allowed_file(file.filename)
    ):
        return False
    file_stream = file.read()
    document = fitz.open(stream=file_stream, filetype="pdf")
    return document


# utility function for initialize_request_file which breaks down the name of a file
# to derive its file type and returns if whether or not the file is a file type contained
# within ALLOWED_EXTENSIONS
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# checks if document has enough space for 1 inch defined margins. The limit variable
# is defined by 2 times an inch (1 inch = 72 pixels for 72 ppi document which is the
# standard)
def check_document_dimensions(document):
    limit = 72 * 2
    for page in document:
        page_width = math.floor(page.rect.width)
        page_height = math.floor(page.rect.height)
        if page_width <= limit or page_height <= limit:
            return False
    return True


# reads the regular payload of the dm
def read_dm_pylibdmtx(image):
    result = pylibdmtx_decode(image)
    if not result:
        return ""
    (decoded, rect) = result[0]
    return decoded.decode("utf-8")


# novel algorithm which reads 3.Sys steganography
def read_steganography(image):
    chunk_size = 2
    width, height = image.size
    image_map = image.load()
    msg = []
    byte = ""
    for i in range(width):
        for j in range(height):
            (r, g, b) = image_map[i, j]
            bin_r = format(r, "08b")
            chunk = bin_r[-chunk_size:]
            byte += chunk
            if len(byte) == 8:
                msg.append(chr(int(byte, 2)))
                byte = ""
    dirty_msg = "".join(msg)
    marker_i = dirty_msg.find("//3.sys//")
    if marker_i != -1:
        return dirty_msg[:marker_i]
    return False


# saves the document to the origpdfs table in 3.Sys db and returns the
# id of that generated row
def save_orig_doc_to_db(document):
    orig_pdf_data = bytes(document.tobytes())
    orig_pdf_metadata = json.dumps(document.metadata)
    QUERY = "INSERT INTO origpdfs (orig_pdf_data, orig_pdf_metadata) VALUES (%s, %s) RETURNING orig_id;"
    try:
        connection = psycopg2.connect(url)
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(QUERY, (orig_pdf_data, orig_pdf_metadata))
                return cursor.fetchone()[0]
    except (Exception, Error) as error:
        return f"Error while connecting to PostgreSQL, {error}"
    finally:
        if connection:
            cursor.close()
            connection.close()


# saves the modified document to the threesyspdf table in 3.Sys db
def save_modified_doc_to_db(metadata, new_pdf_data, steg_id):
    QUERY = "INSERT INTO threesyspdfs (pdf_metadata, pdf_data, origpdfs_id) VALUES (%s,%s, %s) RETURNING *;"
    try:
        connection = psycopg2.connect(url)
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(QUERY, (metadata, new_pdf_data, steg_id))
    except (Exception, Error) as error:
        return f"Error while connecting to PostgreSQL, {error}"
    finally:
        if connection:
            cursor.close()
            connection.close()


# generate a dm with the treepoem module
def generate_dm(pdf_file):
    metadata = pdf_file.metadata
    message = generate_message(metadata)
    return treepoem.generate_barcode(
        barcode_type="datamatrix",
        data=message,
        options={
            "textxalign": "center",
            "textsize": 3,
            "padding": 2,
            "backgroundcolor": "ffffff",
        },
    )


# utility function for generate_dm that generates a secret message
# based on the given metadata to be steganographized
def generate_message(metadata):
    now = datetime.datetime.now()
    author = metadata["author"]
    date_signed = f'{now.strftime("%B")} {now.day}, {now.year}'
    return f"This document was signed using 3-sys API on {date_signed} and is owned by {author}"


# novel steganography function that uses LSB to hide the secret message in the last bits
# (defined by chunk_size) of every pixel, red channel
def steganography(image, secret):
    chunk_size = 2
    # initialize necessary image components
    width, height = image.size
    image_map = image.load()

    # convert secret to workable binary stream
    secret_ascii = msg_to_binary_stream(secret)
    secret_chunks = chunkify(secret_ascii, chunk_size)

    # loop that replaces the least significat n values of each R byte
    # in each pixel with its corresponding secret message chunk of n bits
    for i in range(width):
        for j in range(height):
            secret_chunks_index = i * height + j
            if secret_chunks_index < len(secret_chunks):
                secret_portion = secret_chunks[secret_chunks_index]
                (r, g, b) = image_map[i, j]
                bin_r = format(r, "08b")
                new_bin_r = bin_r[:-chunk_size] + secret_portion
                new_r = int(new_bin_r, 2)
                image.putpixel((i, j), (new_r, g, b))
    return image


# utility function for steganography that converts a string into a binary stream
def msg_to_binary_stream(str):
    formatted_str = str + "//3.sys//"
    ascii_str = "".join(format(ord(i), "08b") for i in formatted_str)
    return ascii_str


# utility function for steganography that splits the given binary_stream into chunk_size
# define chunks
def chunkify(binary_stream, chunk_size):
    return [binary_stream[i: i + chunk_size] for i in range(0, len(binary_stream), chunk_size)]


# attaches generated steg dms to the specified location on the document
def put_steg_dm_in_pdf(pdf_file, steg_dm, dm_steg_location):
    dm_width = 72 - (2 * allowance)
    first_page = pdf_file[0]
    (_x, _y, page_width, page_height) = first_page.rect

    match dm_steg_location:
        case 'top-left':
            x1 = allowance
            y1 = allowance
            x2 = dm_width + allowance
            y2 = dm_width + allowance
        case 'top-right':
            x1 = page_width - dm_width - allowance
            y1 = allowance
            x2 = page_width - allowance
            y2 = allowance + dm_width
        case 'bottom-left':
            x1 = allowance
            y1 = page_height - dm_width - allowance
            x2 = allowance + dm_width
            y2 = page_height - allowance
        case 'bottom-right':
            x1 = page_width - dm_width - allowance
            y1 = page_height - dm_width - allowance
            x2 = page_width - allowance
            y2 = page_height - allowance

    rect = (x1, y1, x2, y2)
    byteIO = io.BytesIO()
    steg_dm.save(byteIO, format="PNG")
    img_bytes = byteIO.getvalue()
    first_page.insert_image(rect, stream=img_bytes)
    return pdf_file


# checks if whether or not the input (unsigned) document has already been previously
# signed by a 3.Sys signature.
def check_if_doc_is_already_prev_signed(document):
    document_metadata = document.metadata
    author = document_metadata["author"]
    creation_date = document_metadata["creationDate"]
    mod_date = document_metadata["modDate"]
    QUERY = "SELECT orig_pdf_metadata -> 'author' AS author, orig_pdf_metadata -> 'creationDate' AS creationdate, orig_pdf_metadata -> 'modDate' AS moddate FROM origpdfs WHERE orig_pdf_metadata ->> 'author' = %s AND orig_pdf_metadata ->> 'creationDate' = %s AND orig_pdf_metadata ->> 'modDate' = %s"
    try:
        connection = psycopg2.connect(url)
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(QUERY, (author, creation_date, mod_date))
        return cursor.rowcount > 0
    except (Exception, Error) as error:
        return f"Error while connecting to PostgreSQL, {error}"
    finally:
        if connection:
            cursor.close()
            connection.close()


# defines if whether or not the document has been modifed
def check_if_document_is_modified(document, dm_stegs):
    if len(dm_stegs) != 1:
        return True
    dm_steg = dm_stegs[0]
    QUERY = "SELECT * FROM threesyspdfs WHERE origpdfs_id = (%s);"
    steg_msg = read_steganography(dm_steg)
    metadata = document.metadata
    try:
        connection = psycopg2.connect(url)
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(QUERY, (steg_msg,))
                if cursor.rowcount > 0:
                    (
                        rpdf_id,
                        rpdf_metadata,
                        rpdf_data,
                        rorigpdfs_id,
                    ) = cursor.fetchall()[0]
                    return not metadata == rpdf_metadata
    except (Exception, Error) as error:
        return f"Error while connecting to PostgreSQL, {error}"
    finally:
        if connection:
            cursor.close()
            connection.close()
