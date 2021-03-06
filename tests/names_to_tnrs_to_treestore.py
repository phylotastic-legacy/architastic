#!/usr/bin/env python
'''
Mark Holder and Derrick Zwickl

Example of a client that:
    1. Takes a list of input taxon names
    2. Sends them to a TNRS following the demo of the TNRS API described at:
        http://www.evoio.org/wiki/Phylotastic/TNRS (optional)
    3. Queries a treestore to obtain a pruned subtree corresponding to those
        taxa


1. Reads names (separated by a mixture of newline or comma characters) from file 
    names passed in as command-line arguments (or reads name from standard 
    input if no arguments are given).

2. Sends intput names to iPlant TNRS, selects from matches based on match score
    as defined by that TNRS (this entire step can be skipped if names are already 
    assumed to be clean). 

3. Sends taxon names on to a treestore.  Currently the hard coded options are 
    the opentree treestore and an rdftreestore.  These both currently return
    a pruned tree for the specified taxa.  However, opentree with do this from
    its large graph that includes taxonomy information across the ToL, while
    the rdf treestore will choose a single tree stored in it that contains the 
    largest number of passed taxa, and prune that.

Passes 

Outputs tab-delimited summary of the matches for each query sorted by score of 
    the match (or the submitted name followed tabs and a newline if no match
    was found).

Writes status message and a summary of the time taken to standard error (note
    that some of the time taken in the summary is the reading and writing of 
    names in this script, not the TNRS service).


treestore.Treestore functions
get_names(self, tree_name=None, format='json')
get_subtree(self, contains=[], match_all=False, format='newick')
'''
import sys, os
try:
    import requests
except ImportError:
    sys.exit('You must install the "requests" package by running\n  pip install requests\n\npip can be obtained from http://pypi.python.org/pypi/pip if you do not have it.')
import time
import datetime
import re
import string
import json
from argparse import ArgumentParser

#this is for used for type checking as a type= argument in argparse.add_argument
def proportion_type(string):
    #it would be nice to be able to pass as specific range, but the func can only take 1 arg
    min_val, max_val = 0.0, 1.0
    value = float(string)
    if value < min_val or value > max_val:
        mess = 'value %f must be between %.2f and %.2f' % (value, min_val, max_val)
        raise ArgumentTypeError(mess)
    return value


#use argparse module to parse commandline input
parser = ArgumentParser(description='run a full (but basic) phylotastic workflow, i.e. input names->TNRS->Treestore')

tnrs_group = parser.add_argument_group('TNRS', 'Options relating to Taxonomic Name Resolution Service query')

tnrs_group.add_argument('--no-tnrs', action='store_true', default=False,
                    help='send the names straight to the treestore without cleaning by the TNRS (default False)')

tnrs_group.add_argument('-m', '--min-match-score', type=proportion_type, default=None,
                    help='minimum score of TNRS match to pass name onto treestore (default 0.5)')

tnrs_group.add_argument('-a', '--pass-all-name-matches', action='store_true', default=False,
                    help='for a given query name, pass all TNRS matches of >= than min-match-score\n\t(default: only pass best)')

parser.add_argument('-v', '--verbose', action='store_true', default=False,
                    help='print a bunch of crap to stderr about tnrs query, etc')

parser.add_argument('-t', '--treestore', type=str, default=None, 
                    help='choose a particular treestore to query. (current options = {opentree, rdftreestore}) default opentree')

parser.add_argument('filenames', nargs="*", default=None, 
                    help='a list of filenames to read for comma or newline delimited taxon names (none for stdin)')

parser.add_argument('-o', '--output', type=str, default=None, 
                    help='file to write output to (default stdout)')

#now process the command line
options = parser.parse_args()

if options.no_tnrs and (options.min_match_score or options.pass_all_name_matches):
    sys.exit('Don\'t pass --min-match-score or --pass-all-name-matches with --no-tnrs')

if options.min_match_score is None:
    options.min_match_score = 0.5

sleep_interval = 1.0
sleep_interval_increase_factor = 1.5

DOMAIN = 'http://api.phylotastic.org'
SUBMIT_PATH = 'tnrs/submit'
SUBMIT_URI = DOMAIN + '/' + SUBMIT_PATH
NAMES_KEY = u'names'
header_written = False
#USE_RDF = 'RDFTREESTORE' in os.environ

tnrs_start_time = time.time()

#open the newline delimited source of taxon names
if len(sys.argv) == 1:
    inp_stream_list = [sys.stdin]
else:
    #inp_stream_list = [open(fn, 'rU') for fn in sys.argv[1:]]
    inp_stream_list = [open(fn, 'rU') for fn in options.filenames]
if len(inp_stream_list) == 0:
    inp_stream_list = [sys.stdin]

def query_treestore(taxon_uid_tuples, treestore_name='http://opentree-dev.bio.ku.edu'):
    '''DJZ
    Query a treestore, making some strict assumptions about what formats they accept
    requests in.  Currently only works with treestores that return pruned trees based
    on a comma delimited list of taxon names.  The opentree treestore accepts this
    stuck in a raw POST.  An RDF treestore is accessed by passing a list of names to
    functions from the treestore python package.
    
    taxon_uid_tuples - list of doubles with (taxon name, uid), only taxon_names
        currently used
    treestore_name - EITHER a url string for an opentree treestore, or a string containing
        'rdf', for an RDF treestore

    returns tree in newick string currently
    '''
    
    if not treestore_name or "opentree" in treestore_name:
        treestore_name = 'http://opentree-dev.bio.ku.edu'

    use_uids = False
    if not use_uids:
        #remove dupe names
        name_list = set([str(tup[0]) for tup in taxon_uid_tuples])
        name_string = ','.join(name_list)
    else:
        exit('sorry, can\'t request by uids yet')
        name_string = ','.join(str(tup[1]) for tup in taxon_uid_tuples)

    if 'opentree' in treestore_name.lower():
        #assuming that this is a url for now
        #port is defined by neo4j
        PORT = 7474

        #probably not stable urls
        #this is what the path was previously with raw text query
        #SUBMIT_PATH = '/db/data/ext/GetJsons/graphdb/subtreeForNames'
        #newest, with cql query
        SUBMIT_PATH = '/db/data/ext/GetJsons/graphdb/subtree'
        
        SUBMIT_URI = treestore_name + ':' + str(PORT) + SUBMIT_PATH

        headers = {'Content-Type':'Application/json'}
        #cql post
        data = '{"query":"pt.taxaForSubtree=\\"%s\\""}' % name_string
        
        if options.verbose:
            sys.stderr.write('querying opentree treestore with %d names ...\n' % len(name_list))
        try:
            resp = requests.post(SUBMIT_URI, headers=headers, data=data)
        except requests.exceptions.ConnectionError as err:
            sys.exit('\nError connecting to treestore %s!\n%s' % (treestore_name, err)) 
        
        #clean up the output.  Surely a better way to do this.
        ret_newick = resp.text
       
        if not ret_newick:
            sys.exit('\ndNo appropriate tree found in treestore %s!\n' % (treestore_name, err)) 

        start_pos = 0
        end_pos = len(ret_newick) - 1
        while ret_newick[start_pos] != '(' and start_pos < end_pos:
            start_pos += 1
        while ret_newick[end_pos] != ')' and end_pos > 0:
            end_pos -= 1

        return ret_newick[start_pos:end_pos + 1]

    elif 'rdf' in treestore_name.lower():
        try:
            import treestore
        except ImportError:
            sys.exit('You must install the "treestore" package to interface with an RDF treestore.\n Get it from https://github.com/phylotastic/rdf-treestore.')

        #instantiate a treestore object, which will look for an attached 
        rdf_treestore = treestore.Treestore()
        
        #taxon names aren't necessarily standardized in trees, can could have underscores, but won't
        #come back from the TRNS that way.  So, if it finds nothing try again. Currently finding nothing
        #is indicated by the throwing of a base Exception in the treestore module
        if options.verbose:
            sys.stderr.write('querying RDF treestore with %d names ...\n' % len(name_list))
        try:
            underscored_list = [ re.sub(' ', '_', name) for name in name_list ]
            ret_tree = rdf_treestore.get_subtree(contains=underscored_list, match_all=False, format='newick')
        except:
            if options.verbose:
                sys.stderr.write('querying RDF treestore with %d names ...\n' % len(name_list))
            ret_tree = rdf_treestore.get_subtree(contains=name_list, match_all=False, format='newick')
        
        return ret_tree
    
    else:
        sys.exit('I don\'t know how to contact treestore %s yet' % treestore_name)


def write_resolved_names(submitted_name_list, names_response, outp):
    '''MTH & DJZ
    Writes the submitted, accepted, matched names and the source and URI
    to `outp` in tab-delimited form.
    '''
    global header_written
    col_fields = [u'submittedName', u'acceptedName', u'matchedName', u'score', u'sourceId', u'uri']
    output_names = ['Submitted', 'Accepted', 'Matched', 'Score', 'Source', 'URI']
    if not header_written:
        outp.write('%s\n' % ''.join('%25s' % f for f in output_names))
        header_written = True
    by_sub_name = {}
    for match in names_response:
        sn = match[u'submittedName']
        by_sub_name[sn] = match[u'matches']
    matches = {}
   
    for sn in submitted_name_list:
        m = by_sub_name.get(sn)
        matches[sn] = m
        if m:
            sorted_list = [(float(i[u'score']), i) for i in m]
            sorted_list.sort(reverse=True)
            for score_float, a_match in sorted_list:
                outp.write('%25s' % sn) 
                outp.write('%s\n' % '\t'.join(['%25s' % a_match[f] for f in col_fields[1:]]))
        else:
            outp.write('%25s\t\t\t\t\t\n' % sn)
    return matches    

#MTH & DJZ
all_tnrs_results = []
all_query_names = []

for inp_stream in inp_stream_list:
    #first split on newlines
    name_list = [unicode(line.strip()) for line in inp_stream if len(line.strip()) > 0]
    #then on commas
    split_list = []
    for n in name_list:
        split_list.extend(n.split(','))
    name_list = [ tax.strip() for tax in split_list ]
    all_query_names.extend(name_list)

    if not options.no_tnrs:
        batch_size = 10
        curr_ind = 0
        while curr_ind < len(name_list): 
            end_ind = curr_ind + batch_size
            if end_ind > len(name_list):
                end_ind = len(name_list)
            this_batch = name_list[curr_ind:end_ind]
            names_newline_sep = '\n'.join(this_batch)
            resp = requests.get(SUBMIT_URI,
                                params={'query':names_newline_sep},
                                allow_redirects=True)
            if options.verbose:
                sys.stderr.write('Sent GET to %s\n' %(resp.url))
            resp.raise_for_status()
            results = resp.json()
            retrieve_uri = results.get(u'uri')
            if retrieve_uri:
                if options.verbose:
                    sys.stderr.write('Retrieving names from %s\n' % (retrieve_uri))
            elif len(resp.history) > 0:
                retrieve_uri = resp.url
            else:
                sys.exit('Did not get a URI or redirect from the submit GET operation. Got:\n %s\n' % str(results))
            min_time = time.time()
            sleep_interval = 1.0
            while retrieve_uri:
                retrieve_response = requests.get(retrieve_uri)
                retrieve_response.raise_for_status()
                retrieve_results = retrieve_response.json()
                if NAMES_KEY in retrieve_results:
                    break
                min_time = time.time()
                if options.verbose:
                    sys.stderr.write('Waiting (%f sec) for processing by tnrs.\n' % sleep_interval)
                time.sleep(sleep_interval)
                sleep_interval *= sleep_interval_increase_factor
            
            if options.verbose:
                write_resolved_names(this_batch, retrieve_results[NAMES_KEY], sys.stderr)
            curr_ind += batch_size
        
            all_tnrs_results.extend(retrieve_results[NAMES_KEY])
        
        tnrs_end_time = time.time()

        taxon_tuples = []
        for sub_tax_dict in all_tnrs_results:
            for single_match_dict in sorted(sub_tax_dict[u'matches'], reverse=True, key=lambda m: float(m['score'])):
                if float(single_match_dict['score']) >= options.min_match_score:
                    taxon_tuples.append((single_match_dict[u'matchedName'], single_match_dict[u'uri']))
                    if not options.pass_all_name_matches:
                        break
    else:
        taxon_tuples = [ (n, None) for n in all_query_names ]

treestore_start_time = time.time()

if len(taxon_tuples) < 2:
    sys.exit('Need at least 2 names to pass to treestore!  Try using the TNRS if you didn\'t')

tree_string = query_treestore(taxon_tuples, treestore_name=options.treestore)

if options.output:
    out_stream = open(options.output, 'w')
    out_stream.write('%s' % tree_string.strip())
else:
    if sys.stdout.isatty():
        sys.stdout.write('%s\n' % tree_string.strip())
    else:
        sys.stdout.write('%s\n' % tree_string.strip())
#out_stream.write('%s\n' % json.dumps(tree_string.strip()))

treestore_end_time = time.time()
if options.no_tnrs:
    tnrs_time = 0.0
else:
    tnrs_time = datetime.timedelta(seconds=(tnrs_end_time - tnrs_start_time))
treestore_time = datetime.timedelta(seconds=(treestore_end_time - treestore_start_time))
if options.verbose:
    sys.stderr.write('%s %s\n' % (string.rjust('tnrs time', 15), string.rjust(str(tnrs_time), 15)))
    sys.stderr.write('%s %s\n' % (string.rjust('treestore time', 15), string.rjust(str(treestore_time), 15)))

