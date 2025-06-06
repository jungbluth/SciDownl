# -*- coding: utf-8 -*-
"""Command line tool of scidownl."""
import os.path

import click

from ..log import get_logger

logger = get_logger()


@click.group()
@click.help_option("-h", "--help")
def cli():
    """Command line tool to download pdfs from Scihub."""
    pass


@cli.command("config")
@click.option("-l", "--location", is_flag=True, help="Show the location of global config file.")
@click.option("-g", "--get", type=(str, str), help="Get config by section and key, "
                                                   "usage: --get <section> <key>.")
@click.help_option("-h", "--help")
def config(location, get):
    """Get global configs."""
    from ..config import get_config, GlobalConfig

    configs = get_config()
    if location:
        logger.info(f"Global config file path: {GlobalConfig.config_fpath}")
        return

    if get:
        sec, key = get
        if sec not in configs.sections():
            logger.warning(f"Section '{sec}' is not found. Valid sections: {configs.sections()}")
            return
        value = configs[sec].get(key, None)
        if value is None:
            logger.warning(f"Key '{key} is not found. Valid keys: {list(dict(configs.items(sec)).keys())}")
            return
        logger.info(f"Value: {configs[sec][key]}")


@cli.command("domain.update")
@click.option("-m", "--mode", default='crawl', help="update mode, could be 'crawl' or 'search',"
                                                    " default mode is 'crawl'.")
@click.help_option("-h", "--help")
def update_domains(mode):
    """Update available SciHub domains and save them to local db."""
    from ..core.updater import scihub_domain_updaters

    updater_cls = scihub_domain_updaters.get(mode, None)
    if updater_cls is None:
        logger.error(f"Update mode (-m) must be one of "
                     f"{list(scihub_domain_updaters.keys())}, got "
                     f"'{mode}' instead.")
        return
    updater = updater_cls()
    updater.update_domains()


@cli.command("domain.list")
@click.help_option("-h", "--help")
def list_domains():
    """List available SciHub domains in local db."""
    import tablib
    from ..db.service import ScihubUrlService

    service = ScihubUrlService()
    urls = service.get_all_urls()
    urls.sort(key=lambda url: url.success_times, reverse=True)
    tab = tablib.Dataset(headers=["Url", "SuccessTimes", "FailedTimes"])
    for url in urls:
        tab.append((url.url, url.success_times, url.failed_times))
    tab_str = tab.export("cli", tablefmt="psql")
    print(tab_str)


@cli.command("download")
@click.option("-d", "--doi", multiple=True,
              help="DOI string. Specifying multiple DOIs is supported, "
                    "e.g., --doi FIRST_DOI --doi SECOND_DOI ... ")
@click.option("-p", "--pmid", multiple=True, type=int,
              help="PMID numbers. Specifying multiple PMIDs is supported, "
                   "e.g., --pmid FIRST_PMID --pmid SECOND_PMID ...")
@click.option("-t", "--title", multiple=True,
              help="Title string. Specifying multiple titles is supported, "
                   "e.g., --title FIRST_TITLE --title SECOND_TITLE ...")
@click.option("-o", "--out",
              help="Output directory or file path, which could be an absolute path "
                   "or a relative path. "
                   "Output directory examples: /absolute/path/to/download/, ./relative/path/to/download/, "
                   "Output file examples: /absolute/dir/paper.pdf, ../relative/dir/paper.pdf. "
                   "If --out is not specified, paper will be downloaded to the current directory "
                   "with the file name of the paper's title. "
                   "If multiple DOIs or multiple PMIDs are provided, the --out option is always considered "
                   "as the output directory, rather than the output file path.")
@click.option("-u", "--scihub-url",
              help="Scihub domain url. If not specified, automatically choose one from local saved domains. "
                   "It's recommended to leave this option empty.")
@click.option("-x", "--proxy",
              help="Proxy with the format of SCHEME=PROXY_ADDRESS. e.g., --proxy http=http://127.0.0.1:7890.")
@click.option("-i", "--input-file",
              help="Path to a file containing one DOI per line. The output failed DOIs will be saved to a file named "
                   "after this file with '_failed.txt' suffix.")
@click.help_option("-h", "--help")
def download(doi, pmid, title, out, scihub_url, proxy: str, input_file=None):
    """Download paper(s) by DOI or PMID."""
    from ..core.task import ScihubTask
    from ..config import get_config

    configs = get_config()

    # Process DOIs from input file if provided
    if input_file:
        try:
            with open(input_file, 'r') as f:
                file_dois = [line.strip() for line in f if line.strip()]
            # Add DOIs from file to the list
            dois_from_args = list(doi)  # convert tuple to list
            doi = tuple(dois_from_args + file_dois)
            logger.info(f"Loaded {len(file_dois)} DOIs from {input_file}")
        except Exception as e:
            logger.error(f"Error reading DOIs from {input_file}: {e}")

    logger.info("Run scihub tasks. Tasks information: ")
    if len(doi) > 0:
        logger.info("%15s: %s" % ("DOI(s)", list(doi)))
    if len(pmid) > 0:
        logger.info("%15s: %s" % ("PMID(s)", list(pmid)))
    if len(title) > 0:
        logger.info("%15s: %s" % ("TITLE(s)", list(title)))

    if out is None:
        logger.info("%15s: %s" % ("Output", os.path.abspath('./')))
    else:
        logger.info("%15s: %s" % ("Output", out))

    if scihub_url is None:
        logger.info("%15s: <auto.%s>" % ("SciHub Url", configs['scihub.task']['scihub_url_chooser_type']))
    else:
        logger.info("%15s: %s" % ("SciHub Url", scihub_url))

    # Always consider out as a directory if there are multiple DOIs and PMIDs.
    if len(doi) + len(pmid) + len(title) > 1:
        if out is not None and out[-1] != "/":
            out = out + '/'

    proxies = {}
    # Load proxies configured in global configurations.
    if configs['proxy'].get('http') is not None:
        proxies['http'] = configs['proxy'].get('http')
    if configs['proxy'].get('https') is not None:
        proxies['https'] = configs['proxy'].get('https')

    # Overwrite the proxy with the user specified proxy.
    if proxy is not None and "=" in proxy:
        scheme, proxy_address = proxy.split("=")[:2]
        proxies[scheme] = proxy_address

    if len(proxies) > 0:
        logger.info("%15s: %s" % ("Proxies", proxies))

    tasks = []
    for doi_item in doi:
        tasks.append({
            'source_keyword': doi_item,
            'source_type': 'doi',
            'scihub_url': scihub_url,
            'out': out,
            'proxies': proxies
        })
    for pmid_item in pmid:
        tasks.append({
            'source_keyword': pmid_item,
            'source_type': 'pmid',
            'scihub_url': scihub_url,
            'out': out,
            'proxies': proxies
        })
    for title_item in title:
        tasks.append({
            'source_keyword': title_item,
            'source_type': 'title',
            'scihub_url': scihub_url,
            'out': out,
            'proxies': proxies
        })
    
    # Initialize counters for tracking progress
    total_attempted = 0
    successful_downloads = 0
    failed_sources = []
    
    logger.info("Starting downloads...")
    for task_kwargs in tasks:
        total_attempted += 1
        task = ScihubTask(**task_kwargs)
        try:
            task.run()
            # Task was successful if status is not an error state
            if task.context.get('status') not in ['crawling_failed', 'extracting_failed', 'downloading_failed']:
                successful_downloads += 1
            else:
                # Record failed DOI/PMID/title
                failed_sources.append(task_kwargs['source_keyword'])
        except Exception as e:
            logger.error(f"final status: {task.context['status']}, error: {task.context['error']}")
            # Record failed DOI/PMID/title
            failed_sources.append(task_kwargs['source_keyword'])
        
        # Log progress every 5 attempts or when the last task is completed
        # Prevent double logging if the total is a multiple of 5
        if (total_attempted % 5 == 0 and total_attempted < len(tasks)) or total_attempted == len(tasks):
            logger.info(f"Progress: {successful_downloads}/{total_attempted} papers downloaded successfully")
    
    # Create a file with failed sources if there are any
    if failed_sources and input_file:
        # Generate output filename based on input file
        base_name, ext = os.path.splitext(input_file)
        failed_file = f"{base_name}_failed{ext}"
        
        # Write failed sources to the file
        try:
            with open(failed_file, 'w') as f:
                for source in failed_sources:
                    f.write(f"{source}\n")
            logger.info(f"Saved {len(failed_sources)} failed DOIs to {failed_file}")
        except Exception as e:
            logger.error(f"Error writing failed DOIs to {failed_file}: {e}")
    
    # Final report
    success_rate = (successful_downloads / total_attempted * 100) if total_attempted > 0 else 0
    logger.info("=" * 50)
    logger.info(f"Download completed. Final report:")
    logger.info(f"Total papers attempted: {total_attempted}")
    logger.info(f"Successfully downloaded: {successful_downloads}")
    logger.info(f"Failed downloads: {len(failed_sources)}")
    logger.info(f"Success rate: {success_rate:.1f}%")
    if failed_sources and input_file:
        logger.info(f"Failed DOIs saved to: {failed_file}")
    logger.info("=" * 50)


if __name__ == '__main__':
    cli()
