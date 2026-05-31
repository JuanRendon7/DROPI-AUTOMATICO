from celery import Celery

celery_app = Celery("dropi_sales_machine")
celery_app.config_from_object("app.celeryconfig")
