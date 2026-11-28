"""#1313: a Work that uses an added skill is told to name the method in passing, on every load.

Before, only the load that added a reference skill carried the tell-in-passing note, so a later Work
(the skill already added) or a long Work that reloaded it had nothing to keep the method in its answer.
Evidence class: model-free unit tests over the real ``SkillLibrary``/``SkillBinding``/``Capabilities``
with the fake GitHub transport of ``test_skill_auto_adopt_1291``.
"""
from personal_agent.messages import msg
import test_skill_auto_adopt_1291 as base

SKILL = base.SKILL


class MethodNote(base.AutoAdopt):
    def test_a_later_work_using_the_added_skill_is_told_to_name_the_method(self):
        first = self.caps().execute('skill_load', {'skill': SKILL})
        self.assertTrue(first['adopted'])
        self.assertIn(msg('skills.adopted_note', skill='internal-comms'), first['note'])
        # A later Work: the skill is installed now, so nothing is adopted, and the note still asks.
        later = self.caps(self.library.binding()).execute('skill_load', {'skill': SKILL})
        self.assertNotIn('adopted', later)
        self.assertIn(msg('skills.method_note', skill='internal-comms'), later['note'])
        self.assertNotIn(msg('skills.adopted_note', skill='internal-comms'), later['note'])

    def test_reloading_in_the_same_work_repeats_the_note(self):
        caps = self.caps()
        caps.execute('skill_load', {'skill': SKILL})
        again = caps.execute('skill_load', {'skill': SKILL})
        self.assertIn(msg('skills.method_note', skill='internal-comms'), again['note'])

    def test_a_bundled_host_skill_gets_no_method_note(self):
        self.library.set_enabled(True)
        binding = self.library.binding()
        self.assertIn('agentos/agentos-skills', binding.entries)
        loaded = self.caps(binding).execute('skill_load', {'skill': 'agentos/agentos-skills'})
        self.assertNotIn(msg('skills.method_note', skill='agentos-skills'), loaded['note'])
