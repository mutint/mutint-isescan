from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('mutint_experiment', '__first__'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='IsescanRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('options', models.JSONField(default=dict)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('installed', 'Installed'), ('unchanged', 'Unchanged'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], default='queued', max_length=16)),
                ('task_result_id', models.CharField(blank=True, max_length=64)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('removed', models.IntegerField(blank=True, null=True)),
                ('added', models.IntegerField(blank=True, null=True)),
                ('error', models.TextField(blank=True)),
                ('log', models.TextField(blank=True)),
                ('annotation_sha256', models.CharField(blank=True, max_length=64)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('experiment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='isescan_runs', to='mutint_experiment.experiment')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
