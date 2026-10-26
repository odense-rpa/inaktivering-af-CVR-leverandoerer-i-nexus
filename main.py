import asyncio
import logging

from automation_server_client import (
    AutomationServer,
    Workqueue,
    Credential,
    WorkItemStatus,
)
from kmd_nexus_client import NexusClientManager
from datafordeler import DatafordelerGraphQL
from odk_tools.tracking import Tracker
from rapidfuzz.distance import Levenshtein
from odk_tools.reporting import report

logger = logging.getLogger(__name__)
nexus: NexusClientManager | None = None
datafordeler: DatafordelerGraphQL | None = None
tracker: Tracker | None = None
proces_navn = "Inaktivering af CVR-leverandører i Nexus"


def beregn_levenshtein(nexus_navn: str, cvr_navn: str) -> float:
    """Beregn Levenshtein-afstand som procentdel af Nexus-navnets længde.

    Matcher Blue Prism-logikken: (distance / Len(nexus_navn)) * 100.
    Returnerer en procentværdi, f.eks. 9.5 for 9.5%.
    """
    if not nexus_navn or not cvr_navn:
        raise ValueError("Begge navne skal være ikke-tomme strenge.")

    distance = Levenshtein.distance(nexus_navn.lower(), cvr_navn.lower())
    return (distance / len(nexus_navn)) * 100


async def populate_queue(workqueue: Workqueue):
    logger = logging.getLogger(__name__)

    logger.info("Hello from populate workqueue!")

    alle_leverandører = nexus.nexus_client.get(
        "https://odensekommune.nexus.kmd.dk/api/core/mobile/odensekommune/v2/suppliers"
    ).json()
    # behold alle hvor cvrNumber != "" AND active == true
    aktive_leverandører = [
        leverandør
        for leverandør in alle_leverandører
        if leverandør.get("cvrNumber") and leverandør.get("active") and leverandør["type"] == "external"
    ]
    for leverandør in aktive_leverandører:
        self = nexus.nexus_client.get(leverandør["_links"]["self"]["href"]).json()

        data = {
            "id": leverandør["id"],
            "CVR": leverandør["cvrNumber"],
            "navn": leverandør["name"],
            "P-nummer": self.get("productNumber"),
        }
        workqueue.add_item(data=data, reference=data["CVR"])


async def process_workqueue(workqueue: Workqueue):
    logger = logging.getLogger(__name__)

    logger.info("Hello from process workqueue!")

    for item in workqueue:
        with item:
            data = item.data  # Item data deserialized from json as dict

            try:
                # --- Step 1: Check if P-number exists ---
                if not data.get("P-nummer"):
                    report(
                        report_id="inaktivering_af_cvr_leverandoerer_i_nexus",
                        group="Manuel",
                        json={"CVR": data["CVR"], "Årsag": "Intet p-nummer i Nexus"},
                    )
                    tracker.track_partial_task(process_name=proces_navn)
                    continue

                p_number = data["P-nummer"]

                # --- Step 2: Fetch CVR data ---
                try:
                    virksomhed = datafordeler.hent_virksomhed(data["CVR"])
                except ValueError:
                    virksomhed = None

                if virksomhed is None or not virksomhed.navn:
                    report(
                        report_id="inaktivering_af_cvr_leverandoerer_i_nexus",
                        group="Manuel",
                        json={
                            "CVR": data["CVR"],
                            "Årsag": f"Virksomhed ikke fundet i CVR: {data['CVR']}",
                        },
                    )
                    tracker.track_partial_task(process_name=proces_navn)
                    continue

                cvr_company_name = virksomhed.navn

                # --- Step 3: First Levenshtein check (Nexus Name vs CVR Main Name) ---
                cvr_name_score = beregn_levenshtein(data["navn"], cvr_company_name)
                if cvr_name_score < 15:
                    # CVR name matches — no further name checks needed
                    pass
                else:
                    # CVR name mismatch — try P-unit name as fallback

                    # --- Step 4: Find matching P-unit ---
                    p_enhed = virksomhed.find_produktionsenhed(str(int(p_number)))

                    if not p_enhed:
                        report(
                            report_id="inaktivering_af_cvr_leverandoerer_i_nexus",
                            group="Manuel",
                            json={
                                "CVR": data["CVR"],
                                "P-nummer": p_number,
                                "Årsag": f"Angivne P-nummer kunne ikke bekræftes: {p_number}",
                            },
                        )
                        tracker.track_partial_task(process_name=proces_navn)
                        continue

                    # --- Step 5: Second Levenshtein check (Nexus Name vs P-Unit Name) ---
                    p_unit_name = p_enhed.navn
                    if p_unit_name:
                        p_name_score = beregn_levenshtein(data["navn"], p_unit_name)
                        if p_name_score >= 15:
                            report(
                                report_id="inaktivering_af_cvr_leverandoerer_i_nexus",
                                group="Manuel",
                                json={
                                    "CVR": data["CVR"],
                                    "P-nummer": p_number,
                                    "Årsag": f"Leverandørnavn i Nexus og navn fra datafordeleren stemmer ikke overens: {data['navn']} vs {p_unit_name}",
                                },
                            )
                            tracker.track_partial_task(process_name=proces_navn)
                            continue
                    else:
                        logger.warning(
                            f"P-unit name missing for P-number {p_number}, skipping name check"
                        )

                # --- Step 6: All checks passed - Proceed with inactivation ---
                logger.info(f"Valideret: {data['navn']} (CVR: {data['CVR']})")

                # Hvis virksomheden ikke er aktiv i CVR, så inaktiver i Nexus
                if not virksomhed.er_aktiv:
                    leverandør = nexus.nexus_client.get(
                        f"https://odensekommune.nexus.kmd.dk:443/api/core/mobile/odensekommune/v2/suppliers/{data['id']}"
                    ).json()
                    leverandør = nexus.nexus_client.get(
                        leverandør["_links"]["self"]["href"]
                    ).json()
                    leverandør["active"] = False
                    nexus.organisationer.opdater_leverandør(leverandør)
                    tracker.track_task(process_name=proces_navn)

            except Exception as e:
                logger.error(f"Error processing item: {data}. Error: {e}")
                item.fail(str(e))
                tracker.track_partial_task(process_name=proces_navn)


async def main():
    global nexus, datafordeler, tracker

    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    # Initialize external systems for automation here..
    tracking_credential = Credential.get_credential("Odense SQL Server")
    nexus_credential = Credential.get_credential("KMD Nexus - produktion")
    datafordeler_credential = Credential.get_credential("Datafordeler")

    tracker = Tracker(
        username=tracking_credential.username, password=tracking_credential.password
    )

    nexus = NexusClientManager(
        client_id=nexus_credential.username,
        client_secret=nexus_credential.password,
        instance=nexus_credential.data["instance"],
        timeout=60,
    )

    datafordeler = DatafordelerGraphQL(
        client_id=datafordeler_credential.username,
        client_secret=datafordeler_credential.password,
    )

    # Parse command line arguments
    parser = argparse.ArgumentParser(description=proces_navn)
    parser.add_argument(
        "--queue",
        action="store_true",
        help="Populate the queue with test data and exit",
    )
    args = parser.parse_args()

    # Queue management
    if args.queue:
        workqueue.clear_workqueue(WorkItemStatus.NEW)
        await populate_queue(workqueue)
        return

    # Process workqueue
    await process_workqueue(workqueue)


if __name__ == "__main__":
    import argparse

    asyncio.run(main())
