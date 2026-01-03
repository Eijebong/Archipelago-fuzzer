"""
This Fuzzer Hook detects lambda (and regular function) variable capture issues by getting the values of nonlocal
 variables (aka capture variables, aka free variables) as rules are set on locations and entrances, and then compares
 the values of the nonlocal variables again once the multiworld has finished generating, or whenever a new rule is set
 on the location/entrance.

Location.access_rule and Entrance.access_rule are monkey-patched into `property`-like descriptors to intercept set
 rules.
worlds.generic.Rules.add_rule is monkey-patched to intercept added rules.
worlds.generic.Rules.add_rule had to be monkey-patched by replacing its `.__code__` due to the issue that importing
 worlds.generic.Rules causes all other worlds to import before the worlds.generic.Rules import resolves, and many of
 those worlds import worlds.generic.Rules.add_rule directly.

There are a couple of cases that this Hook cannot cover.
- If the rules are defined in a separate loop (and a capture issue occurs there), and then the rules are set onto
  Locations/Entrances in a second loop, this Hook will only see the rules being set after the capture issue has already
  occurred.
- If a rule is defined before a nonlocal variable it references has been defined, there is nothing for the Hook to
  compare against. In this case, the Hook tries to keep track of these rules by recording them onto the MultiWorld
  object, but the Hook cannot do this if the Location/Entrance the rule was set on does not yet belong to a Region that
  belongs to the MultiWorld.
"""


import logging
import sys
import traceback
from typing import Any
from types import CodeType, CellType

import worlds.generic.Rules
from BaseClasses import MultiWorld, Location, Entrance
from worlds.generic.Rules import CollectionRule


from fuzz import BaseHook, GenOutcome


Spot = Location | Entrance

DO_EXTRA_LOGGING = False
"""Enables some extra logging when rules are set."""

EMPTY_CELL_SENTINEL = object()
"""Signifies that the CellType references an unbound variable."""

RULE_RECORDS_ATTR = "_fuzzer_rule_records"
"""The name of the attribute added onto Location and Entrance instances to store rule information."""

MW_RULES_WITH_UNBOUND_NONLOCAL_VARIABLES_ATTR = "_fuzzer_spots_with_unbound_capture_variables"
"""The name of the attribute set onto the MultiWorld instance to track rules with unbound capture variables."""

FUZZER_ACCESS_RULE_ATTR = "_fuzzer_access_rule"
"""
The name of the attribute set onto Location and Entrance instances to store access rules now that .access_rule is a
property-like descriptor.
"""


class VariableCaptureError(RuntimeError):
    pass


class FuzzerAccessRuleRecord:
    spot: Location | Entrance  # Included here to help with debugging
    rule: CollectionRule  # Included here to help with debugging
    code: CodeType  # Duplicated here to help with debugging
    closure: tuple[CellType, ...]
    initial_closure_variables: dict[str, Any]
    debug_info: str

    def __init__(self,
                 spot: Location | Entrance,
                 rule: CollectionRule,
                 code: CodeType,
                 closure: tuple[CellType, ...],
                 debug_info: str):
        self.spot = spot
        self.rule = rule
        self.code = code
        self.closure = closure
        self.debug_info = debug_info
        self.traceback = "".join(traceback.format_stack())

        has_unbound_closure_variables = False
        if code:
            initial_closure_variables = {}
            for k, v in zip(code.co_freevars, self.safe_get_cell_contents(closure)):
                if v is EMPTY_CELL_SENTINEL:
                    has_unbound_closure_variables = True
                initial_closure_variables[k] = v
            self.initial_closure_variables = initial_closure_variables
        else:
            self.initial_closure_variables = {}

        if DO_EXTRA_LOGGING and has_unbound_closure_variables:
            for k, v in self.initial_closure_variables.items():
                if v is EMPTY_CELL_SENTINEL:
                    logging.warning(f"The initial definition of a rule on {self.spot} was defined before its"
                                    f" nonlocal variable %s was set. The Fuzzer Hook may not be able to detect variable"
                                    f" capture issues with this rule.", k)

        # Try to update already known records with unbound closure variables, and append `self` if it has unbound
        # closure variables.
        # This is only possible if the `spot` has access to the `MultiWorld` instance.
        if (region := spot.parent_region) and (multiworld := region.multiworld):
            unbound_records: list[FuzzerAccessRuleRecord]
            unbound_records = getattr(multiworld, MW_RULES_WITH_UNBOUND_NONLOCAL_VARIABLES_ATTR, [])

            # Update the records known to contain unbound closure variables.
            unbound_records = [record for record in unbound_records if record.recheck_closure_variables()]

            if has_unbound_closure_variables:
                # This record has unbound closure variables, so it should be checked again when new rules are set,
                # because its unbound closure variables might be set by that point.
                unbound_records.append(self)

            # Update the records known to contain unbound variables.
            setattr(multiworld, MW_RULES_WITH_UNBOUND_NONLOCAL_VARIABLES_ATTR, unbound_records)

    @staticmethod
    def safe_get_cell_contents(cells: tuple[CellType, ...]) -> Any:
        for cell in cells:
            try:
                yield cell.cell_contents
            except ValueError:
                # "Value Error: Cell is empty" is raised if the cell is empty.
                # This can happen if a variable is only defined *after* the closure.
                # e.g.
                #
                # def outer_func():
                #     def func1():
                #         func2()
                #
                #     # func1 has an empty cell because func2 is not defined yet
                #
                #     def func2():
                #         pass
                #
                #     # func1's cell now contains func2 because func2 has been defined.
                # This happens in alttp.Rules with tr_big_key_chest_keys_needed only being defined after the rule that
                # uses it.
                yield EMPTY_CELL_SENTINEL

    def recheck_closure_variables(self) -> bool:
        """
        Recheck the closure variables, raising a VariableCaptureError if a bound closure variable has changed since the
        Record was initially created.

        Returns whether the record's closure variables include some which are unbound.
        """
        still_has_unbound_closure_variables = False
        closure_variables = {}
        for k, v in zip(self.code.co_freevars, self.safe_get_cell_contents(self.closure)):
            if self.initial_closure_variables.get(k) is EMPTY_CELL_SENTINEL:
                if v is not EMPTY_CELL_SENTINEL:
                    # Update the initial closure variables to include whatever value has been defined since the rule was
                    # initially defined. It is possible that the closure variable could have changed multiple times
                    # before recheck_closure_variables has been called, but there is no way to know.
                    self.initial_closure_variables[k] = v
                else:
                    still_has_unbound_closure_variables = True
            closure_variables[k] = v

        if closure_variables != self.initial_closure_variables:
            raise VariableCaptureError(f"The closure variables have changed since the rule was defined on {self.spot}"
                                       f" (extra info: {self.debug_info})."
                                       f"\nInitial closure variables: {self.initial_closure_variables}"
                                       f"\nCurrent closure variables: {closure_variables}"
                                       f"\nStack trace from when the rule was set:"
                                       f"\n{self.traceback}")
        return still_has_unbound_closure_variables


class Hook(BaseHook):
    failed: bool
    raised: VariableCaptureError | None

    def setup_worker(self, args):
        super().setup_worker(args)

        # The patched add_rule needs access to some module-level globals from this Hook module. To account for this file
        # being named differently, it is put into sys.modules under a fixed name.
        sys.modules["_fuzzer_add_rule_intercept"] = sys.modules[__name__]

        # Patch ap methods/classes to detect rules that have been set.
        self.patch_access_rule_as_property()
        self.patch_add_rule()
        # set_rule just does `spot.access_rule = rule`, so the patched .access_rule handles it.
        # self.patch_set_rule()

    def before_generate(self, args):
        super().before_generate(args)
        # Initialise per-generation attributes.
        # FuzzerAccessRuleRecords are set onto the Locations/Entrances the rules are set on, as well as an attribute on
        # the MultiWorld instance, instead of being set on attributes on the Hook.
        self.failed = False
        self.raised = None

    @staticmethod
    def patch_access_rule_as_property():
        from BaseClasses import Location, Entrance

        default_rule = Location.access_rule

        del Location.access_rule
        del Entrance.access_rule

        # Getting location.access_rule with no rule set returns Location.{FUZZER_ACCESS_RULE_ATTR}, if this
        # `is Location.access_rule`, then rules get optimised better in `add_rule`.
        setattr(Location, FUZZER_ACCESS_RULE_ATTR, staticmethod(default_rule))
        setattr(Entrance, FUZZER_ACCESS_RULE_ATTR, staticmethod(default_rule))

        class FuzzerExtendedProperty:
            """Almost the same as `property`, but extended to include class access."""

            def __get__(self, instance, owner):
                if instance is None:
                    # Class access.
                    # add_rule checks for `(old_rule := location.access_rule) is Location.access_rule`, so that when no
                    # rule has been set, add_rule can optimise the combined rule.
                    # This must return the same value as `location.access_rule` for a location whose .access_rule has
                    # not been set.
                    return default_rule
                else:
                    # Instance access.
                    return getattr(instance, FUZZER_ACCESS_RULE_ATTR)

            def __set__(self, instance, value):
                setattr(instance, FUZZER_ACCESS_RULE_ATTR, value)
                Hook.add_rule_record(instance, value, "set_rule(),"
                                                      " .access_rule = <new_rule>,"
                                                      " or add_rule() with no existing rule")

        prop = FuzzerExtendedProperty()

        Location.access_rule = prop
        Entrance.access_rule = prop

    @staticmethod
    def add_rule_record(spot: Spot, rule: CollectionRule, debug_info: str):
        code: CodeType | None
        closure: tuple[CellType, ...]
        if code := getattr(rule, "__code__", None):
            if closure := getattr(rule, "__closure__", ()):
                record = FuzzerAccessRuleRecord(spot, rule, code, closure, debug_info)

                # FuzzerAccessRuleRecords are stored on the Location/Entrance because there is no guarantee that the
                # Location/Entrance has been added to the multiworld yet, and it is desirable for
                # FuzzerAccessRuleRecords to get garbage collected alongside the multiworld itself.
                rule_records: list[FuzzerAccessRuleRecord]
                if not hasattr(spot, RULE_RECORDS_ATTR):
                    rule_records = [record]
                    setattr(spot, RULE_RECORDS_ATTR, rule_records)
                else:
                    rule_records = getattr(spot, RULE_RECORDS_ATTR)
                    # This re-check isn't very useful for a lot of cases because the data is being stored on the spot
                    # itself, but this might increase the chances of catching cases where a rule is defined that
                    # references a variable that is only defined after the rule is defined.
                    # This happens in alttp.Rules with tr_big_key_chest_keys_needed only being defined after the rule
                    # that uses it.
                    # Re-check here to try to cause an exception at the point at which a rule is added, providing a
                    # stacktrace that can be followed.
                    for record in rule_records:
                        record.recheck_closure_variables()
                    rule_records.append(record)
                if DO_EXTRA_LOGGING:
                    logging.info("Adding rule record to %s with initial closure variables %s",
                                 spot, record.initial_closure_variables)
            else:
                if DO_EXTRA_LOGGING:
                    logging.info("Skipping adding rule record to %s because it has no closure variables", spot)
        else:
            if DO_EXTRA_LOGGING:
                logging.info("Could not add rule record to %s", spot)

    @staticmethod
    def patch_add_rule():
        def intercept_add_rule(spot: Spot, rule: CollectionRule, combine: str = "and"):
            # Copy-paste of old add_rule.
            old_rule = spot.access_rule
            # empty rule, replace instead of add
            if old_rule is Location.access_rule or old_rule is Entrance.access_rule:
                # New Comment: The patched .access_rule property will handle adding the rule record.
                spot.access_rule = rule if combine == "and" else old_rule
            else:
                # Patched Code:
                # `worlds.generic.Rules.add_rule.__closure__` is read-only, so closure variables cannot be added, so the
                # import is required, otherwise the add_rule's replaced code cannot access `Hook` or
                # `FUZZER_ACCESS_RULE_ATTR`.
                # noinspection PyUnresolvedReferences
                from _fuzzer_add_rule_intercept import Hook, FUZZER_ACCESS_RULE_ATTR

                # Avoid hitting the patched .access_rule property and recording the combined rule lambda, by accessing
                # .fuzzer_access_rule directly.
                if combine == "and":
                    setattr(spot, FUZZER_ACCESS_RULE_ATTR, lambda state: rule(state) and old_rule(state))
                else:
                    setattr(spot, FUZZER_ACCESS_RULE_ATTR, lambda state: rule(state) or old_rule(state))

                debug_info = f"add_rule() using combine={combine}"
                Hook.add_rule_record(spot, rule, debug_info)

        # Most worlds will have imported `add_rule` before it is possible to patch worlds.generic.Rules.add_rule, so
        # this horrible hack of replacing the __code__ is needed.
        worlds.generic.Rules.add_rule.__code__ = intercept_add_rule.__code__

    # @staticmethod
    # def patch_set_rule():
    #     def intercept_set_rule(spot: Spot, rule: CollectionRule):
    #         # Copy-paste of old set_rule.
    #         spot.access_rule = rule
    #
    #         # Patched code.
    #         debug_info = "set_rule()"
    #
    #         # __closure__ is read-only, so closure variables cannot be added, so the import is required, otherwise the
    #         # set_rule's replaced code cannot access `Hook`.
    #         from fuzz_hook_lambda_capture import Hook
    #         Hook.add_rule_record(spot, rule, debug_info)
    #
    #     # Most worlds will have imported `set_rule` before it is possible to patch worlds.generic.Rules.add_rule, so
    #     # this horrible hack of replacing the __code__ is needed.
    #     worlds.generic.Rules.set_rule.__code__ = intercept_set_rule.__code__

    def after_generate(self, mw: MultiWorld, output_path):
        super().after_generate(mw, output_path)

        if not mw:
            # Multiworld failed to generate for some other reason.
            return

        try:
            for spots in (mw.get_locations(), mw.get_entrances()):
                for spot in spots:
                    rule_record: FuzzerAccessRuleRecord
                    for rule_record in getattr(spot, RULE_RECORDS_ATTR, ()):
                        # Raises VariableCaptureError if bound closure variables have changed in value.
                        rule_record.recheck_closure_variables()
        except VariableCaptureError as ex:
            self.failed = True
            self.raised = ex

    def reclassify_outcome(self, outcome, raised):
        if self.failed and outcome == GenOutcome.Success:
            return GenOutcome.Failure, self.raised
        else:
            return super().reclassify_outcome(outcome, raised)
