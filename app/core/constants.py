# ** ------------------------------ Celery Setup --------------------------
CEL_DEFAULT_QUEUE = "my_lab"
CEL_MAIN_NAME = "lab_queue_"
CEL_TASK_PATHS = ["app.job.tasks"]
# ** ------------------------------ Celery Setup:END --------------------------


# ** ------------------------------ DB Setup --------------------------
# App Database Configuration
DB_ECHO = True
DB_POOL_SIZE = (5,)
DB_MAX_OVERFLOW = 10
DB_POOL_TIMEOUT = 30

# Celery Database Configuration
DB_CEL_ECHO = True
DB_CEL_POOL_SIZE = (5,)
DB_CEL_MAX_OVERFLOW = 10
DB_CEL_POOL_TIMEOUT = 30
# ** ------------------------------ DB Setup:END --------------------------
