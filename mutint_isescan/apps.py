from django.apps import AppConfig


class IsescanConfig(AppConfig):
    name = 'mutint_isescan'

    def ready(self):
        from django.urls import include, re_path
        from mutint_common.about_registry import register_about_section
        from mutint_common.annotator_registry import register_reference_annotator
        from mutint_common.plugin_registry import register_plugin_urlpatterns
        from mutint_isescan import annotator
        from mutint_isescan.version import __version__

        # The whole UI: a panel on the Import data page's reference tabs, drawn by core, that
        # runs after a reference lands when ticked, or on demand from the Update Annotation
        # tab. What `run` does is queue a job; the panel's body lists the runs.
        register_reference_annotator(
            self, name='isescan', label='ISEScan IS elements',
            run=annotator.run, clean=annotator.clean,
            template='isescan/panel.html', context=annotator.panel_context,
            description=('Predicts insertion sequences with ISEScan and annotates them as '
                         'mobile_element features, as breseq CONVERT-REFERENCE -s does.'))
        # Two JSON endpoints the panel polls and posts to; no page of its own.
        register_plugin_urlpatterns([
            re_path(r'^isescan/', include('mutint_isescan.urls')),
        ])
        register_about_section(self, name='mutint-isescan', version=__version__,
                               template='about/sections/mutint_isescan.html')

        # Nothing else is registered, and each absence is a decision:
        #
        # **No nav entry and no import tab.** What this does is a step on the reference, and
        # the reference tabs are where a person is already looking at it; a page of its own
        # would be a place to go to do something the Import data page already offers.
        #
        # **No import handler.** Nothing is dropped; the input is the stored reference.
        #
        # **No rebuilder.** It derives nothing from the mutations. What it installs goes
        # through core's `install_annotation`, which asks for every registered rebuild itself.
        #
        # **No export type, no example dataset.** It adds no mutation type, and an example
        # would have to run ISEScan to demonstrate anything.
