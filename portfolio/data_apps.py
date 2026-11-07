import csv
import io
import re

def safe_csv(headers, records, *, include_header=True):

    def safe(value):
        if re.fullmatch('-[0-9]+(?:\\.[0-9]+)?', value):
            return value
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r')) else value
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    if include_header:
        writer.writerow([safe(h) for h in headers])
    writer.writerows([[safe(value) for value in row] for row in records])
    return output.getvalue()
