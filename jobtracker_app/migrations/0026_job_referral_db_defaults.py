from django.db import migrations


class Migration(migrations.Migration):
    """
    Restore PostgreSQL defaults on referral Job columns.

    Django AddField backfills existing rows then drops the DB DEFAULT, leaving
    NOT NULL columns. Live gunicorn still runs pre-referral Job.objects.create()
    and omits apply_referral_discount, which becomes NULL and 500s.
    """

    dependencies = [
        ("jobtracker_app", "0025_job_referral_fields"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE jobtracker_app_job
                    ALTER COLUMN apply_referral_discount SET DEFAULT TRUE,
                    ALTER COLUMN referral_discount_amount SET DEFAULT 0.00,
                    ALTER COLUMN referral_credit_amount SET DEFAULT 0.00;
                UPDATE jobtracker_app_job
                    SET apply_referral_discount = TRUE
                    WHERE apply_referral_discount IS NULL;
            """,
            reverse_sql="""
                ALTER TABLE jobtracker_app_job
                    ALTER COLUMN apply_referral_discount DROP DEFAULT,
                    ALTER COLUMN referral_discount_amount DROP DEFAULT,
                    ALTER COLUMN referral_credit_amount DROP DEFAULT;
            """,
        ),
    ]
