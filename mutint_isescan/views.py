"""The panel's endpoints: the run list it polls, deleting a finished run, and a run's files."""

import os

from django.http import Http404, JsonResponse
from django.views.decorators.http import require_POST

import mutint_sample.views.common
from mutint_experiment.models import Experiment
from mutint_experiment.permissions import (
    can_edit_experiment,
    can_view_project,
    experiment_lock_refusal,
)
from mutint_common.fileserve import serve_file

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
    """Delete a run and its directory. A live one is refused: the `post_delete` receiver
    removes the directory a running ISEScan is writing into.

    **Live is what the queue says, not what the column says** -- `annotator.in_flight`. A run
    whose worker was killed keeps `status="running"` for ever, and refusing to delete it on
    that basis left the experiment with a run it could neither finish nor remove nor start
    another beside."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)
    isescan_run = IsescanRun.objects.filter(pk=pk).select_related("experiment").first()
    if isescan_run is None:
        return JsonResponse({"error": "Unknown run."}, status=404)
    if not can_edit_experiment(request.user, isescan_run.experiment):
        return JsonResponse(
            {"error": experiment_lock_refusal(isescan_run.experiment)
                      or "You cannot change this experiment."}, status=403)
    if (not isescan_run.is_finished
            and annotator.in_flight(isescan_run.experiment) == isescan_run):
        return JsonResponse(
            {"error": "That run has not finished. Cancel it first -- the notice above the "
                      "Import data tabs has the button -- then delete it."}, status=409)
    isescan_run.delete()
    return JsonResponse({"deleted": pk})


def run_file(request, pk, name):
    """One of the files ISEScan left under a finished run's `out/`, served inline.

    Read access to the project is the bar, as it is for the reference download: the
    prediction is evidence for an annotation every reader of the experiment already sees.
    `name` is checked against `output_files()` rather than resolved against the directory,
    so nothing a client typed reaches the filesystem; 404 throughout, the posture the run
    list takes, so the endpoint cannot say which run ids exist. Every ISEScan output is text,
    so they are served as such rather than left to the extension table, which knows nothing
    of `.faa`, `.sum` or `.raw`.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "You must be signed in."}, status=403)
    isescan_run = IsescanRun.objects.filter(pk=pk).select_related("experiment").first()
    if isescan_run is None or not can_view_project(request.user, isescan_run.experiment.project):
        raise Http404("No such run.")
    if not isescan_run.is_finished or name not in isescan_run.output_files():
        raise Http404("No such file.")
    path = os.path.join(isescan_run.output_dir(), name)
    return serve_file(request, path, os.path.basename(name),
                      content_type="text/plain; charset=utf-8")
