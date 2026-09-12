"""Two JSON endpoints for the panel: the run list it polls, and deleting a finished run."""

from django.http import JsonResponse
from django.views.decorators.http import require_POST

import mutint_sample.views.common
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import (
    can_edit_experiment,
    can_view_project,
    experiment_lock_refusal,
)

from mutint_isescan import annotator
from mutint_isescan.models import IsescanRun


def _experiment_or_none(request):
    try:
        return mutint_sample.views.common.get_experiment(request)
    except (Experiment.DoesNotExist, ValueError):
        return None


def runs(request):
    """`{runs: [...]}` for `?experiment_id=`, for the panel's poll."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)
    experiment = _experiment_or_none(request)
    if experiment is None:
        return JsonResponse({"error": "Unknown experiment."}, status=404)
    if not can_view_project(request.user, experiment.project):
        return JsonResponse({"error": "You cannot see this experiment."}, status=403)
    return JsonResponse({"runs": annotator.run_rows(experiment)})


@require_POST
def run_delete(request, pk):
    """Delete a finished run and its directory. An unfinished one is refused: the
    `post_delete` receiver removes the directory a live ISEScan is writing into."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)
    isescan_run = IsescanRun.objects.filter(pk=pk).select_related("experiment").first()
    if isescan_run is None:
        return JsonResponse({"error": "Unknown run."}, status=404)
    if not can_edit_experiment(request.user, isescan_run.experiment):
        return JsonResponse(
            {"error": experiment_lock_refusal(isescan_run.experiment)
                      or "You cannot change this experiment."}, status=403)
    if not isescan_run.is_finished:
        return JsonResponse(
            {"error": "That run has not finished. Cancel it on the Jobs page first, then "
                      "delete it."}, status=409)
    isescan_run.delete()
    return JsonResponse({"deleted": pk})
