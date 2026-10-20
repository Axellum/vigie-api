# 0002. Official warnings: the issuer's words intact, localized offline

- Status: accepted
- Date: 2026-10-20

## Context

The national weather services publish official warnings (MeteoAlarm for 39 European countries, the NWS for the United
States). A display or an app wants "the warnings for my position", with the colour and the type, and the words of the
service. Two risks: rewriting an official text (a translation, a shortening, a level of our own) and presenting it as
if it were the service's; and answering "no warning" when the API merely cannot tell whether a position is inside an
area. The areas come as polygons, or only as codes (MeteoAlarm `EMMA_ID`, NUTS3, NUTS2; NWS UGC zones), and the files
that give the shape of a code have different licences, some none at all.

## Decision

1. **The official content stays intact, ours stands beside it.** The identifier, the times, the original level and
   event, the headline, the description and the instruction (with their language), the codes of the area and the link to
   the original are returned as given. The three fields the API derives (`status`, `level`, `phenomenon`) come from
   tables, are listed in `derived`, and are `null` when no table covers the source's word. Rewriting is not an option:
   the terms of MeteoAlarm also want modified information to go with the unmodified one.
2. **Every warning carries its credit; the answer carries the notice the terms impose.** The issuer's name is added
   from its CAP document (`senderName`).
3. **A position is matched by the polygon of the source, or by the shape of the code of the area, both tested offline**
   (shapely, bundled files, nothing read from a service at run time). A code whose shape this instance does not have
   cannot be matched: the warning has `area.localization = none`, and the answer counts such warnings
   (`coverage[].warnings_not_matchable`) instead of letting an empty list read as an all-clear. By country, such a
   warning is still listed.
4. **Only shapes with a stated licence that a public repository can pass on are bundled:** Natural Earth (public
   domain) and Wikidata's NUTS codes (CC0). Eurostat GISCO (non-commercial, a notice to carry) and the MeteoAlarm
   `EMMA_ID` file (no licence in its repository, and the terms license the Information, not the Content) are not.
   An operator may build the `EMMA_ID` file for his own instance.
5. **Few requests:** the country of a position is found offline so that only the needed MeteoAlarm feeds are asked;
   a CAP document is asked once and only for a warning an answer returns; the NWS warnings are read from the answer the
   hazards already ask for.
6. **A change is pushed only for a followed position,** and the first look at a position sets a baseline silently. A
   source that does not answer never ends a warning.

## Consequences

- Germany, Italy, Spain, Austria, Poland and the other countries that name their area with `EMMA_ID` codes only cannot
  be matched to a position until the shapes are licensed or built by the operator. This is stated in every answer that
  concerns them, in the README and in `SOURCES.md`.
- The API gains a dependency: shapely (and numpy with it).
- The embedded frame, whose contract is fixed, does not carry the warnings: they have their own routes.
