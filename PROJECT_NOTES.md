# TSW HUD - Project Notes

## Current Version
**v0.1.0** — Flask + pywebview architecture with theme system and API proxying

## Known Trains v2 Redesign (Planned Major Version Bump)
Three HTML preview mockups approved in `design_previews/`:
- Groups/Subclasses/Individuals hierarchy
- Two independently-settable speeds per locomotive
- Per-class power type configuration
- Status dots and photo slots

**Status**: Fully designed, awaiting v3.0.0 implementation

## Open Investigations

### 1. Ammeter Panel Scaling
**Issue**: Raw ammeter value from Class 66 was ~7030 amps  
**Known**: Real traction ammeter shows 0-8 kA range  
**To Investigate**: Determine exact scale factor for ammeter panel display  
**Impact**: Ammeter readout accuracy  

### 2. Locomotive Image Filename Convention
**Issue**: Image file naming scheme for locomotive classes unclear  
**Hypothesis**: Filenames match raw RVD identifier  
**To Investigate**: Confirm naming pattern; validate against known class names  
**Impact**: Automated locomotive image loading  

### 3. Timetable Export File Format
**Known**: Format documented in `output_format.rs` from another TSW app  
**To Investigate**: Obtain and validate a real timetable export file  
**Status**: OCR-based timetable import was built then removed (confirmed scheduled times unavailable via API)  
**Impact**: Timetable display feature feasibility  

### 4. GSM-R Node Telemetry
**Issue**: Feasibility of retrieving GSM-R cab radio node data  
**To Investigate**: Check API endpoints for GSM-R availability  
**Impact**: Cab radio simulation feature  

## API Capabilities (Confirmed)
- `HUD_GetSpeed` — current speed (m/s)
- `DriverAid.Data` — gradient, speed limit, distance to signal, signal aspect
- `DriverAid.PlayerInfo` — GPS coordinates
- `DriverAid.TrackData` — station names and distances
- `WeatherManager.*` — temperature, precipitation, cloudiness, fog density
- `CurrentDrivableActor.ObjectClass` — raw locomotive class name
- `IS_GetVehicleInfo` — clean display name, max speed, vehicle metadata
- `HUD_GetAmmeter` — traction amps (divide by 1000 for kA)
- `TimeOfDay.data` — local time, sunrise/sunset times

**Not Available via API** (requires binary asset extraction):
- Scheduled stop times (confirmed via Rust source code analysis)

## Theme System
**Current**: Single purple theme (fixed, non-configurable)  
**Theme Order**: purple, green, blue, amber, crimson, teal, rose, slate, rainbow  
- New themes insert above slate
- Slate always second-to-last
- Rainbow always last

## Development Workflow
1. **UI/aesthetic changes**: Visual preview first (rendered HTML + Playwright screenshot)
2. **Functional changes**: Discuss plan, then implement
3. **All changes**: Test against real captured API data before shipping
4. **Regression tests**: Run before every packaged release

## Files to Retain Until Work Is Shipped
- `design_previews/` — Contains approved Known Trains v2 mockups
- `PROJECT_NOTES.md` — Tracks in-progress investigations and redesign plans
