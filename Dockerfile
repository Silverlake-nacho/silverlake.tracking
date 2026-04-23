# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Prevent Python from writing pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \


# Set the working directory in the container
WORKDIR /app

# Install production dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source
COPY . .

# Expose the port Render will listen on
EXPOSE 5000

# Optional mount point for persistent uploads/database storage.
VOLUME ["/app/data"]

# Run the web server
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
