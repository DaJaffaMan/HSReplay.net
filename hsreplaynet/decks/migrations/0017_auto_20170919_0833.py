# -*- coding: utf-8 -*-
# Generated by Django 1.11.5 on 2017-09-19 08:33
from __future__ import unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('decks', '0016_clustersetsnapshot_training_run_id'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='archetypetrainingdeck',
            name='deck',
        ),
        migrations.RemoveField(
            model_name='signature',
            name='archetype',
        ),
        migrations.RemoveField(
            model_name='signaturecomponent',
            name='card',
        ),
        migrations.RemoveField(
            model_name='signaturecomponent',
            name='signature',
        ),
        migrations.RemoveField(
            model_name='archetype',
            name='active_in_standard',
        ),
        migrations.RemoveField(
            model_name='archetype',
            name='active_in_wild',
        ),
        migrations.DeleteModel(
            name='ArchetypeTrainingDeck',
        ),
        migrations.DeleteModel(
            name='Signature',
        ),
        migrations.DeleteModel(
            name='SignatureComponent',
        ),
    ]
