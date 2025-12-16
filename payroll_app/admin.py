from django.contrib import admin
from .models import EmployeeProfile, CollaborationRate, TimeEntry, Payout, PayrollSettings

admin.site.register(EmployeeProfile)
admin.site.register(CollaborationRate)
admin.site.register(TimeEntry)
admin.site.register(Payout)
admin.site.register(PayrollSettings)

# Register your models here.
