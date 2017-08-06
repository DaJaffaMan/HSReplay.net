from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views.generic import View
from hsreplaynet.games.models import GameReplay
from .models import UploadEvent


class UploadDetailView(View):
	def get(self, request, shortid):
		replay = GameReplay.objects.find_by_short_id(shortid)
		if replay:
			return HttpResponseRedirect(replay.get_absolute_url())

		try:
			upload = UploadEvent.objects.get(shortid=shortid)
			if upload.game:
				return HttpResponseRedirect(upload.game.get_absolute_url())
		except UploadEvent.DoesNotExist:
			# It is possible the UploadEvent hasn't been created yet.
			upload = None

		request.head.title = "Uploading replay..."

		context = {}
		context["upload"] = upload
		context["redirect_url"] = request.build_absolute_uri(request.path)

		return render(request, "uploads/processing.html", context)
