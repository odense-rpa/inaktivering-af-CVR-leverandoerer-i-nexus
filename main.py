import asyncio
import logging
import sys
import os

from automation_server_client import AutomationServer, Workqueue, WorkItemError, Credential, WorkItemStatus
from kmd_nexus_client import NexusClientManager
from datafordeler import Datafordeler as DatafordelerClient
from odk_tools.tracking import Tracker
from rapidfuzz.distance import Levenshtein
from odk_tools.reporting import report

logger = logging.getLogger(__name__)
nexus: NexusClientManager | None = None
datafordeler: DatafordelerClient | None = None
tracker: Tracker | None = None
proces_navn = "Inaktivering af CVR leverandører i Nexus"


def beregn_levenshtein(addresse1: str, addresse2: str) -> float:
    """Beregn normaliseret Levenshtein-afstand (0=identisk, 1=helt forskellig)."""
    if not addresse1 or not addresse2:
        raise ValueError("Begge adresser skal være ikke-tomme strenge.")
    
    distance = Levenshtein.distance(addresse1.lower(), addresse2.lower())
    max_length = max(len(addresse1), len(addresse2))
    return distance / max_length if max_length > 0 else 0.0


async def populate_queue(workqueue: Workqueue):
    logger = logging.getLogger(__name__)

    logger.info("Hello from populate workqueue!")

    alle_leverandører = nexus.nexus_client.get("https://odensekommune.nexus.kmd.dk/api/core/mobile/odensekommune/v2/suppliers").json()
    # keep alle where cvrNumber != "" AND active == true
    aktive_leverandører = [leverandør for leverandør in alle_leverandører if leverandør.get("cvrNumber") and leverandør.get("active")]
    for leverandør in aktive_leverandører:
        self = nexus.nexus_client.get(leverandør["_links"]["self"]["href"]).json()

        data = {
            "id" : leverandør["id"],
            "CVR": leverandør["cvrNumber"],
            "navn": leverandør["name"],
            "P-nummer": self.get("pNumber"),
        }
        workqueue.add_item(data=data, reference=data["CVR"])
        
    print("hej")

async def process_workqueue(workqueue: Workqueue):
    logger = logging.getLogger(__name__)

    logger.info("Hello from process workqueue!")

    for item in workqueue:
        with item:
            data = item.data  # Item data deserialized from json as dict
            skal_opdateres = False
 
            try:
                if data["P-nummer"] is None:
                    report(
                        report_id="inaktivering_af_cvr_leverandører_i_nexus", # findes ikke atm
                        group="Manuel",
                        json={
                            "CVR": data["CVR"],
                            "Årsag": "Ingen P-nummer tilknyttet leverandør"
                        }
                    )
                
                # CVR-oplysninger:
                cvr_info = datafordeler.hent_CVR_oplysninger(data["CVR"])
                if cvr_info["virksomhedsnavn"]["vaerdi"] is None:
                    report(
                        report_id="inaktivering_af_cvr_leverandører_i_nexus", # findes ikke atm
                        group="Manuel",
                        json={
                            "CVR": data["CVR"],
                            "Årsag": f"Virksomhed findes ikke: {data['CVR']}"
                        }
                    )
                # Beregn Levenshtein-afstand mellem navnet i Nexus og navnet i CVR-oplysningerne
                levenshtein_score = beregn_levenshtein(data["navn"], cvr_info["virksomhedsnavn"]["vaerdi"])
                if levenshtein_score < 0.1:
                    p_enheder = cvr_info.get("produktionsenheder", [])
                    # finder den P-nummer i CVR-oplysningerne, der matcher det P-nummer, der er knyttet til leverandøren i Nexus
                    p_enhed = next((enhed for enhed in p_enheder if enhed.get("pNummer") == data["P-nummer"]), None)
                    if not p_enhed: # Hvis der ikke findes en matchende P-enhed i CVR-oplysningerne, rapporter da til manuel behandling
                        report(
                            report_id="inaktivering_af_cvr_leverandører_i_nexus", # findes ikke atm
                            group="Manuel",
                            json={
                                "CVR": data["CVR"],
                                "Årsag": f"Angivne P-nummer kunne ikke bekræftes: {data['P-nummer']}"
                            }
                        )
                    else:

                

                # Process the item here
                pass
            except WorkItemError as e:
                # A WorkItemError represents a soft error that indicates the item should be passed to manual processing or a business logic fault
                logger.error(f"Error processing item: {data}. Error: {e}")
                item.fail(str(e))


async def main():
    global nexus, datafordeler, tracker

    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    # Initialize external systems for automation here..
    tracking_credential = Credential.get_credential("Odense SQL Server")
    nexus_credential = Credential.get_credential("KMD Nexus - produktion")

    tracker = Tracker(
        username=tracking_credential.username, password=tracking_credential.password
    )

    nexus = NexusClientManager(
        client_id=nexus_credential.username,
        client_secret=nexus_credential.password,
        instance=nexus_credential.data["instance"],
        timeout=60,
    )

    # Initialize Datafordeler with certificates
    certifikat_sti = os.getenv("CERTIFIKATER", "/certifikater")
    datafordeler = DatafordelerClient(
        certifikat_sti=os.path.join(certifikat_sti, "datafordeler.crt"),
        certifikat_nøglefil=os.path.join(certifikat_sti, "datafordeler.key"),
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
