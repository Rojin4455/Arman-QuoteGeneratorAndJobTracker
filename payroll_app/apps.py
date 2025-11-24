from django.apps import AppConfig


class PayrollAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'payroll_app'

    def ready(self):
        import payroll_app.signals  # Import signals to register them
